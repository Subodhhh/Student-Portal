import { useState } from "react";
import { ClassTargetSelect } from "../components/classLevels";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import ScopeBar from "../components/ScopeBar";
import { AudienceFields, PaperPicker } from "../components/testAudience";
import { Alert, Badge, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, PageHeader, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api, type Page } from "../services/api";
import { audienceBody, audienceReady, emptyAudience, newRequestKey, subjectsInAll, type AudienceState, type CheckError, type ScheduledTest, type ScopeResponse } from "../services/createTest";

// Test -> Weekly Test: one month of recurring tests planned at once. Same configuration as a Daily Test (audience, subjects and papers,
// duration, marking) plus the recurrence. The server computes the dates, schedules one test per date (never twice, even on retry) and
// generates each test's exam shortly before it opens, using each paper's latest published version at that time.

type Series = {
  series_id: string; name: string; description: string | null; status: string; month: string; weekdays: number[]; start_time: string; timezone: string;
  window_minutes: number; duration_minutes: number; generate_lead_minutes: number; marking_mode: string; marks_correct: number | null; marks_wrong: number | null;
  branches: string[]; planned_count: number; scheduled_count: number; missing_occurrences: string[]; states: Record<string, number>;
  last_generated_at: string | null; last_generation_error: string | null; created_at: string; cancelled_at: string | null; cancel_reason: string | null;
  tests?: ScheduledTest[];
};
type Preview = { ok: boolean; eligible_count: number; occurrences: { date: string; start_at: string; end_at: string }[]; problems: CheckError[];
  warnings: CheckError[]; conflicts: { plan_id: string; name: string; start_at: string; end_at: string }[] };

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const fmt = (v: string) => new Date(v).toLocaleString();
const describe = (s: Series) => `${s.weekdays.map((d) => DAYS[d].slice(0, 3)).join(", ")} at ${s.start_time} (${s.timezone})`;

function nextMonth() {
  const d = new Date();
  d.setMonth(d.getMonth() + 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function zones(): string[] {
  // The browser's list uses ICU's canonical names (e.g. Asia/Calcutta); the common modern names are added so both can be chosen.
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const all = new Set([...(intl.supportedValuesOf ? intl.supportedValuesOf("timeZone") : []), "Asia/Kolkata", "UTC"]);
  all.delete(local);
  return [local, ...[...all].sort()];
}

export default function WeeklyTestPage() {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const list = useApi<Page<Series>>(`/api/v1/test-series?page=${page}&page_size=20`);
  return (
    <div className="stack">
      <PageHeader subtitle="Plan a month of tests at once. Each date becomes its own scheduled test; its exam is generated shortly before it opens."
        actions={can("test.create") && <Link className="btn" to="/tests/weekly/new">New Weekly Test</Link>} />
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(s) => s.series_id}
            empty={<EmptyState title="No Weekly Tests yet" hint={can("test.create") ? "Create one to schedule a month of tests." : undefined} />}
            columns={[
              { header: "Weekly Test", render: (s) => <><Link to={`/tests/weekly/${s.series_id}`}><strong>{s.name}</strong></Link><div className="muted small">{s.branches.join(", ")}</div></> },
              { header: "Month", render: (s) => s.month },
              { header: "When", render: describe },
              { header: "Scheduled", render: (s) => `${s.scheduled_count} of ${s.planned_count}` },
              { header: "Status", render: (s) => <><StatusBadge status={s.status} />{s.last_generation_error && <div><Badge tone="danger">Generation failed</Badge></div>}
                {s.status === "ACTIVE" && s.missing_occurrences.length > 0 && <div className="muted small">{s.missing_occurrences.length} not scheduled yet</div>}</> },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
    </div>
  );
}

export function NewWeeklyTestPage() {
  const scope = useScope();
  if (scope.loading) return <Spinner />;
  return (
    <>
      <PageHeader crumbs={[<Link key="w" to="/tests/weekly">Weekly Test</Link>, "New"]} title="New Weekly Test"
        subtitle="Choose the audience, the subjects and their papers, the days of the month and the time. Preview the dates, then create." />
      <ScopeBar branch={false} />
      {!scope.providerId ? <EmptyState title="No provider available" hint="There is no provider you can create tests for." />
        : <SeriesForm key={scope.providerId} providerId={scope.providerId} />}
    </>
  );
}

function SeriesForm({ providerId }: { providerId: string }) {
  const navigate = useNavigate();
  const sc = useApi<ScopeResponse>(`/api/v1/tests/scope?provider_id=${providerId}`);
  const [aud, setAud] = useState<AudienceState>(emptyAudience);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [papers, setPapers] = useState<Record<string, string>>({});            // subject -> paper_id (insertion order = paper order)
  const [cfg, setCfg] = useState({ month: nextMonth(), weekdays: [5] as number[], start: "10:00", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    window: "120", duration: "60", lead: "60", marking: "UNIFORM" as "UNIFORM" | "PER_QUESTION", correct: "4", wrong: "1", exam_type: "", target_class: "" });
  const [preview, setPreview] = useState<{ key: string; data: Preview } | null>(null);
  const [errors, setErrors] = useState<CheckError[]>([]);
  const [request, setRequest] = useState<{ key: string; body: string } | null>(null);
  const act = useAction();

  if (sc.loading && !sc.data) return <Spinner />;
  if (sc.error || !sc.data) return <Alert kind="error">{sc.error || "Could not load what you may target."}</Alert>;
  if (sc.data.branches.length === 0) return <EmptyState title="Nothing to target" hint="You have no branch in this provider where you may create tests." />;
  const scopeData = sc.data;
  const allowed = subjectsInAll(scopeData, aud.branchIds);
  const subjectIds = Object.keys(papers).filter((sid) => allowed.some((s) => s.subject_id === sid));
  const uniform = cfg.marking === "UNIFORM";
  const body = {
    audience: audienceBody(providerId, aud.branchIds, aud.wholeIds, aud.batchIds, [...aud.students.keys()]), name, description: description.trim() || null,
    subjects: subjectIds.map((sid) => ({ subject_id: sid, paper_id: papers[sid] })), month: cfg.month, weekdays: [...cfg.weekdays].sort(),
    start_time: cfg.start, timezone: cfg.timezone, window_minutes: Number(cfg.window), duration_minutes: Number(cfg.duration),
    generate_lead_minutes: Number(cfg.lead), marking_mode: cfg.marking, marks_correct: uniform ? Number(cfg.correct) : null, marks_wrong: uniform ? Number(cfg.wrong) : null,
    exam_type: cfg.exam_type.trim() || null, target_class: cfg.target_class.trim() || null,
  };
  const json = JSON.stringify(body);
  const fresh = preview && preview.key === json ? preview.data : null;
  const incomplete = audienceReady(aud) ?? (name.trim().length < 3 ? "Enter a name (at least 3 characters)." : null)
    ?? (subjectIds.length === 0 ? "Choose at least one subject." : subjectIds.some((sid) => !papers[sid]) ? "Choose a paper for every subject." : null)
    ?? (cfg.weekdays.length === 0 ? "Choose at least one day of the week." : null)
    ?? (!(Number(cfg.duration) > 0) || Number(cfg.duration) > Number(cfg.window) ? "The duration must be positive and fit inside the window." : null);

  const toggleSubject = (sid: string) => setPapers((p) => { const n = { ...p }; if (sid in n) delete n[sid]; else n[sid] = ""; return n; });
  const toggleDay = (d: number) => setCfg((c) => ({ ...c, weekdays: c.weekdays.includes(d) ? c.weekdays.filter((x) => x !== d) : [...c.weekdays, d] }));
  const failWith = (e: unknown) => {
    if (e instanceof ApiError && Array.isArray(e.extra.errors)) { setErrors(e.extra.errors as CheckError[]); throw new Error(e.message, { cause: e }); }
    throw e;
  };
  const runPreview = () => act.run(async () => {
    setErrors([]);
    try { setPreview({ key: json, data: await api<Preview>("/api/v1/test-series/preview", { method: "POST", body }) }); } catch (e) { failWith(e); }
  });
  const create = () => act.run(async () => {
    setErrors([]);
    const key = request && request.body === json ? request.key : newRequestKey();    // a retry of the SAME request reuses its key
    setRequest({ key, body: json });
    try {
      const s = await api<Series>("/api/v1/test-series", { method: "POST", body, headers: { "Idempotency-Key": key } });
      navigate(`/tests/weekly/${s.series_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "GENERATION_FAILED" && typeof e.extra.series_id === "string") {
        navigate(`/tests/weekly/${e.extra.series_id}`);                               // saved; the detail page shows the failure and the retry
        return;
      }
      failWith(e);
    }
  });

  return (
    <div className="stack">
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {errors.length > 0 && <Alert kind="error"><ul className="plain">{errors.map((e, i) => <li key={i}>{e.message}</li>)}</ul></Alert>}
      <Card>
        <h2>1. Who takes these tests?</h2>
        <p className="muted small">Resolved again for every test when its exam is generated, so students who join or leave the batches later are included or dropped.</p>
        <AudienceFields providerId={providerId} scope={scopeData} value={aud} onChange={setAud} />
      </Card>
      <Card>
        <h2>2. Name, subjects and papers</h2>
        <Field label="Name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} hint="Each test is named after it, with its date." />
        <TextAreaField label="Instructions for students (optional)" value={description} maxLength={4000} onChange={(e) => setDescription(e.target.value)} />
        {aud.branchIds.length === 0 ? <p className="muted small">Choose the branches first.</p> : allowed.length === 0 ? <EmptyState title="No subject is available in all selected branches" /> : (
          <div className="check-grid">{allowed.map((s) => (
            <label key={s.subject_id}><input type="checkbox" checked={s.subject_id in papers} onChange={() => toggleSubject(s.subject_id)} /> {s.name}</label>
          ))}</div>
        )}
        {subjectIds.map((sid) => (
          <PaperPicker key={sid} providerId={providerId} subjectId={sid} label={`Paper for ${allowed.find((s) => s.subject_id === sid)?.name ?? "subject"}`}
            value={papers[sid]} needPublished={false} onChange={(p) => setPapers((x) => ({ ...x, [sid]: p?.paper_id ?? "" }))} />
        ))}
        {subjectIds.length > 0 && <p className="muted small">Every test uses the paper's latest published version at the time its exam is generated: publish a new version
          each week to give students a new paper. A subject without a published version is left out of that test and the responsible staff are notified.</p>}
      </Card>
      <Card>
        <h2>3. Schedule</h2>
        <div className="grid cols-3">
          <Field label="Month" type="month" value={cfg.month} onChange={(e) => setCfg({ ...cfg, month: e.target.value })} />
          <Field label="Opens at (local time)" type="time" value={cfg.start} onChange={(e) => setCfg({ ...cfg, start: e.target.value })} />
          <SelectField label="Time zone" value={cfg.timezone} onChange={(e) => setCfg({ ...cfg, timezone: e.target.value })}>
            {zones().map((z) => <option key={z} value={z}>{z}</option>)}
          </SelectField>
        </div>
        <fieldset><legend>Days of the week</legend>
          <div className="check-grid">{DAYS.map((d, i) => (
            <label key={d}><input type="checkbox" checked={cfg.weekdays.includes(i)} onChange={() => toggleDay(i)} /> {d}</label>
          ))}</div>
        </fieldset>
        <div className="grid cols-3">
          <Field label="Open for (minutes)" type="number" min={1} max={1440} value={cfg.window} onChange={(e) => setCfg({ ...cfg, window: e.target.value })} hint="How long students may start the test." />
          <Field label="Duration (minutes)" type="number" min={1} max={720} value={cfg.duration} onChange={(e) => setCfg({ ...cfg, duration: e.target.value })} />
          <Field label="Generate the exam (minutes before)" type="number" min={0} max={10080} value={cfg.lead} onChange={(e) => setCfg({ ...cfg, lead: e.target.value })} />
        </div>
      </Card>
      <Card>
        <h2>4. Marking</h2>
        <div className="grid cols-2">
          <SelectField label="Marking" value={cfg.marking} onChange={(e) => setCfg({ ...cfg, marking: e.target.value as "UNIFORM" | "PER_QUESTION" })}>
            <option value="UNIFORM">Same marks for every question</option><option value="PER_QUESTION">Marks from the question paper</option>
          </SelectField>
          {uniform && <>
            <Field label="Marks for a correct answer" type="number" min={1} max={100} value={cfg.correct} onChange={(e) => setCfg({ ...cfg, correct: e.target.value })} />
            <Field label="Marks deducted for a wrong answer" type="number" min={0} max={100} value={cfg.wrong} onChange={(e) => setCfg({ ...cfg, wrong: e.target.value })} />
          </>}
          <Field label="Exam type (optional)" value={cfg.exam_type} onChange={(e) => setCfg({ ...cfg, exam_type: e.target.value })} />
          <ClassTargetSelect value={cfg.target_class} onChange={(v) => setCfg({ ...cfg, target_class: v })} />
        </div>
      </Card>
      <Card>
        <div className="card-head"><h2>5. Preview and create</h2>
          <Button variant="secondary" busy={act.busy} disabled={!!incomplete} onClick={() => void runPreview()}>Preview dates</Button></div>
        {incomplete && <p className="muted small">{incomplete}</p>}
        {fresh && <SeriesPreview p={fresh} />}
        <div className="row" style={{ marginTop: "var(--space-4)" }}>
          <Button busy={act.busy} disabled={!fresh || !fresh.ok} onClick={() => void create()}>Create Weekly Test</Button>
          {!fresh && !incomplete && <span className="muted small">Preview the dates first.</span>}
        </div>
      </Card>
    </div>
  );
}

function SeriesPreview({ p }: { p: Preview }) {
  return (
    <div className="stack">
      {p.problems.length > 0 && <Alert kind="error"><ul className="plain">{p.problems.map((e, i) => <li key={i}>{e.message}</li>)}</ul></Alert>}
      {p.warnings.map((w, i) => <Alert key={i} kind="warn">{w.message}</Alert>)}
      {p.conflicts.length > 0 && <Alert kind="warn">These tests of the same branches overlap some dates (allowed, but check it is intended):
        <ul className="plain">{p.conflicts.map((c) => <li key={c.plan_id}>{c.name}: {fmt(c.start_at)} – {fmt(c.end_at)}</li>)}</ul></Alert>}
      <p><strong>{p.occurrences.length}</strong> test(s) for <strong>{p.eligible_count}</strong> eligible student(s) at present:</p>
      <DataTable rows={p.occurrences} rowKey={(o) => o.start_at} columns={[
        { header: "Date", render: (o) => new Date(`${o.date}T00:00:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }) },
        { header: "Opens", render: (o) => fmt(o.start_at) }, { header: "Closes", render: (o) => fmt(o.end_at) },
      ]} />
    </div>
  );
}

export function WeeklyTestDetailPage() {
  const { seriesId } = useParams();
  const { can } = useAuth();
  const s = useApi<Series>(`/api/v1/test-series/${seriesId}`);
  const act = useAction();
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  if (s.loading && !s.data) return <Spinner />;
  if (s.error || !s.data) return <Alert kind="error">{s.error || "Weekly Test not found."}</Alert>;
  const d = s.data;
  const generate = () => act.run(async () => {
    const r = await api<Series & { generation: { created: string[]; existing: string[] } }>(`/api/v1/test-series/${d.series_id}/generate`, { method: "POST" });
    setNotice(r.generation.created.length ? `Scheduled ${r.generation.created.length} missing test(s).` : "Every test is already scheduled; nothing was duplicated.");
    s.reload();
  });
  const cancel = () => act.run(async () => {
    const r = await api<{ stopped: number }>(`/api/v1/test-series/${d.series_id}/cancel`, { method: "POST", body: { reason } });
    setNotice(`Cancelled. ${r.stopped} test(s) that had not been generated were stopped; generated exams are unchanged.`);
    setCancelling(false);
    s.reload();
  });
  return (
    <div className="stack">
      <PageHeader crumbs={[<Link key="w" to="/tests/weekly">Weekly Test</Link>, d.name]} title={d.name}
        subtitle={`${d.month} · ${describe(d)} · open ${d.window_minutes} min · ${d.duration_minutes} min test`}
        actions={can("test.create") && d.status === "ACTIVE" && <div className="row">
          <Button variant="secondary" busy={act.busy} onClick={() => void generate()}>Generate missing tests</Button>
          <Button variant="danger" onClick={() => setCancelling(true)}>Cancel remaining tests</Button></div>} />
      {notice && <Alert kind="success">{notice}</Alert>}
      {act.error && !cancelling && <Alert kind="error">{act.error}</Alert>}
      {d.last_generation_error && <Alert kind="error">Scheduling stopped with an error: {d.last_generation_error}. Tests already scheduled are unchanged; choose “Generate missing tests” to retry.</Alert>}
      {d.status === "CANCELLED" && <Alert kind="warn">Cancelled {d.cancelled_at ? fmt(d.cancelled_at) : ""}: {d.cancel_reason}</Alert>}
      <Card>
        <dl className="summary-grid">
          <dt>Status</dt><dd><StatusBadge status={d.status} /></dd>
          <dt>Branches</dt><dd>{d.branches.join(", ")}</dd>
          <dt>Scheduled</dt><dd>{d.scheduled_count} of {d.planned_count} dates{d.missing_occurrences.length ? ` · ${d.missing_occurrences.length} still to schedule` : ""}</dd>
          <dt>Marking</dt><dd>{d.marking_mode === "UNIFORM" ? `+${d.marks_correct} correct, −${d.marks_wrong} wrong, 0 unanswered` : "As in the question papers"}</dd>
          <dt>Exam generated</dt><dd>{d.generate_lead_minutes} minutes before each test opens</dd>
        </dl>
      </Card>
      <DataTable rows={d.tests ?? []} rowKey={(t) => t.plan_id} empty={<EmptyState title="No tests scheduled" />}
        columns={[
          { header: "Opens", render: (t) => fmt(t.start_at) },
          { header: "Test", render: (t) => (t.exam_id ? <Link to={`/exams/${t.exam_id}`}>{t.name}</Link> : t.name) },
          { header: "State", render: (t) => <span title={t.error ?? undefined}><StatusBadge status={t.state} />{t.error && <div className="muted small">{t.error}</div>}</span> },
          { header: "Subjects", render: (t) => t.subjects.join(", ") },
          { header: "Assigned", render: (t) => (t.exam_id ? t.assigned_students : "at generation") },
        ]} />
      {cancelling && (
        <ConfirmDialog title={`Cancel the remaining tests of “${d.name}”?`} danger confirmLabel="Cancel remaining tests" busy={act.busy}
          message="Tests whose exam has not been generated yet are stopped. Exams already generated, attempts and results are not changed."
          onCancel={() => { setCancelling(false); setReason(""); }} onConfirm={() => void cancel()}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        </ConfirmDialog>
      )}
    </div>
  );
}
