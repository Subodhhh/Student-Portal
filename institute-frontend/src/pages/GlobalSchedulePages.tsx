import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isGlobalRole } from "../auth/roles";
import { useScope } from "../auth/ScopeContext";
import type { GlobalTest } from "../components/globalTests";
import ScopeBar from "../components/ScopeBar";
import { AudienceFields } from "../components/testAudience";
import { Alert, Badge, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api, type Page } from "../services/api";
import { audienceBody, audienceReady, emptyAudience, newRequestKey, type AudienceState, type CheckError, type ScopeResponse } from "../services/createTest";
import { audienceText, fmtDate, markingOf, subjectsOf, type Occurrence, type OccurrenceDetail, type SubmittablePaper, type Submission } from "../services/globalSchedule";
import { StudentCount } from "../components/StudentCount";

// Global Tests with a central schedule. The platform plans and publishes the dates; provider staff open a date and submit their own paper
// for each subject and audience. Every rule (who may plan, submit, see what; overlaps; the deadline) is enforced by the API.

type StudentLayouts = {
  scope: string;
  subjects: { subject_id: string; subject: string; position: number }[];
  students: { student_id: string; name: string | null; exam_id: string | null; exam_status: string | null;
    subjects: { subject_id: string; subject: string; status: string; message: string | null; provider_name: string | null }[] }[];
};

const LAYOUT_LABELS: Record<string, string> = {
  NOT_ENROLLED: "Not enrolled", NO_SUBMISSION: "No paper submitted", NOT_APPROVED: "Not approved in time",
  NOT_COVERED: "Not covered", CONFLICT: "Conflict",
};

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function DateState({ o }: { o: Occurrence }) {
  if (o.status !== "PUBLISHED") return <StatusBadge status={o.status} />;
  if (o.generated_at) return <Badge tone="ok">Exams generated</Badge>;
  return o.submissions_open ? <Badge tone="info">Papers open</Badge> : <Badge tone="warn">Generating</Badge>;
}

/** The published dates that concern the caller (all of them for the platform). */
export function GlobalScheduleList() {
  const { me, can } = useAuth();
  const platform = isGlobalRole(me?.primary_role) && can("examplan.manage");
  const [when, setWhen] = useState("upcoming");
  const [page, setPage] = useState(1);
  const list = useApi<Page<Occurrence>>(`/api/v1/global-schedule?when=${when}&page=${page}&page_size=25`);
  return (
    <div className="stack">
      <p className="muted">{platform ? "Every published date with the papers providers have submitted so far."
        : "Dates the platform has published. Open a date to submit your paper for your students before its deadline."}</p>
      <div className="toolbar">
        <SelectField label="Show" value={when} onChange={(e) => { setWhen(e.target.value); setPage(1); }}>
          <option value="upcoming">Upcoming</option><option value="past">Past</option><option value="all">All</option>
        </SelectField>
      </div>
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(o) => o.occurrence_id}
            empty={<EmptyState title={when === "past" ? "No past dates" : "No upcoming Global Test dates"} hint={platform ? "Plan and publish dates on a Global Test's page." : undefined} />}
            columns={[
              { header: "Date", render: (o) => <><strong>{fmtDate(o.start_at)}</strong><div className="muted small">open until {fmtDate(o.end_at)}</div></> },
              { header: "Global Test", render: (o) => <>{o.test_name}<div className="muted small">{o.duration_minutes} min{o.exam_type ? ` · ${o.exam_type}` : ""}</div></> },
              { header: "Subjects", render: (o) => o.subjects.map((s) => (
                <div key={s.subject_id}>{s.subject}{!platform && (s.can_submit ? <span className="muted small"> · you can submit</span> : null)}</div>)) },
              { header: "Paper deadline", render: (o) => (o.status === "PUBLISHED" ? fmtDate(o.submission_deadline) : "—") },
              { header: platform ? "Submissions" : "Your submissions", render: (o) => o.submissions.active },
              { header: "State", render: (o) => <DateState o={o} /> },
              { header: "", render: (o) => <Link className="btn small" to={`/tests/global/occurrences/${o.occurrence_id}`}>
                {!platform && o.submissions_open && o.subjects.some((s) => s.can_submit) ? "Submit paper" : "Open"}</Link> },
            ]} />
          <Pagination page={page} pageSize={25} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
    </div>
  );
}

/** The dates section of a SCHEDULE test's page: planning, single dates, publication, cancellation (platform); published dates (others). */
export function GlobalTestDates({ test, onChanged }: { test: GlobalTest; onChanged: () => void }) {
  const { me, can } = useAuth();
  const platform = isGlobalRole(me?.primary_role) && can("examplan.manage");
  const list = useApi<{ items: Occurrence[] }>(`/api/v1/global-tests/${test.test_id}/occurrences`);
  const [planning, setPlanning] = useState(false);
  const [editing, setEditing] = useState<Occurrence | "new" | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "delete" | "cancel" | "publish"; o?: Occurrence } | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const act = useAction();
  const items = list.data?.items ?? [];
  const drafts = items.filter((o) => o.status === "DRAFT" && new Date(o.start_at) > new Date());
  const refresh = (msg: string) => { setNotice(msg); setConfirm(null); setReason(""); list.reload(); onChanged(); };
  const doConfirm = () => {
    if (!confirm) return;
    void act.run(async () => {
      if (confirm.kind === "delete") {
        await api(`/api/v1/global-occurrences/${confirm.o!.occurrence_id}`, { method: "DELETE" });
        refresh("Draft date deleted.");
      } else if (confirm.kind === "cancel") {
        await api(`/api/v1/global-occurrences/${confirm.o!.occurrence_id}/cancel`, { method: "POST", body: { reason } });
        refresh("Date cancelled. Providers can no longer submit papers for it.");
      } else if (test.status === "DRAFT") {
        await api(`/api/v1/global-tests/${test.test_id}/publish`, { method: "POST", body: {} });
        refresh(`Published “${test.name}” and its ${drafts.length} date(s). Providers can now submit their papers.`);
      } else {
        const r = await api<{ published: number }>(`/api/v1/global-tests/${test.test_id}/occurrences/publish`, { method: "POST", body: {} });
        refresh(`Published ${r.published} more date(s).`);
      }
    });
  };
  return (
    <Card>
      <div className="card-head"><h2>2. Schedule</h2>
        {platform && test.status !== "WITHDRAWN" && <div className="row">
          <Button variant="secondary" onClick={() => setPlanning(true)}>Plan dates for months</Button>
          <Button variant="secondary" onClick={() => setEditing("new")}>Add one date</Button>
          {drafts.length > 0 && <Button onClick={() => setConfirm({ kind: "publish" })}>{test.status === "DRAFT" ? "Publish test and dates" : `Publish ${drafts.length} draft date(s)`}</Button>}
        </div>}
      </div>
      <p className="muted small">{platform ? "Draft dates can be changed or deleted; publishing makes them visible to providers. A published date can be cancelled until its exams are generated at the paper deadline."
        : "Open a date to submit your paper for it."}</p>
      {notice && <Alert kind="success">{notice}</Alert>}
      {act.error && !confirm && <Alert kind="error">{act.error}</Alert>}
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <DataTable rows={items} rowKey={(o) => o.occurrence_id} empty={<EmptyState title="No dates yet" hint={platform ? "Plan the dates for one or several months." : undefined} />}
          columns={[
            { header: "Date", render: (o) => <><strong>{fmtDate(o.start_at)}</strong><div className="muted small">open until {fmtDate(o.end_at)}</div></> },
            { header: "Paper deadline", render: (o) => fmtDate(o.submission_deadline) },
            { header: "Submissions", render: (o) => o.submissions.active },
            { header: "State", render: (o) => <>{<DateState o={o} />}{o.cancel_reason && <div className="muted small">{o.cancel_reason}</div>}
              {o.generation_error && <div className="muted small">Generation error: {o.generation_error}</div>}</> },
            { header: "", render: (o) => (
              <div className="row">
                {o.status !== "DRAFT" && <Link className="btn small secondary" to={`/tests/global/occurrences/${o.occurrence_id}`}>Open</Link>}
                {platform && o.status === "DRAFT" && <Button small variant="secondary" onClick={() => setEditing(o)}>Edit</Button>}
                {platform && o.status === "DRAFT" && <Button small variant="danger" onClick={() => setConfirm({ kind: "delete", o })}>Delete</Button>}
                {platform && o.status === "PUBLISHED" && !o.generated_at && <Button small variant="danger" onClick={() => setConfirm({ kind: "cancel", o })}>Cancel date</Button>}
              </div>) },
          ]} />
      )}
      {planning && <PlanDatesDialog test={test} onClose={() => setPlanning(false)} onDone={(n, existing) => { setPlanning(false); refresh(`Planned ${n} new draft date(s)${existing ? ` (${existing} already planned)` : ""}.`); }} />}
      {editing && <DateDialog test={test} occurrence={editing === "new" ? null : editing} onClose={() => setEditing(null)}
        onDone={() => { setEditing(null); refresh(editing === "new" ? "Draft date added." : "Draft date changed."); }} />}
      {confirm && (
        <ConfirmDialog busy={act.busy} danger={confirm.kind !== "publish"} onCancel={() => { setConfirm(null); setReason(""); }} onConfirm={doConfirm}
          title={confirm.kind === "publish" ? (test.status === "DRAFT" ? `Publish “${test.name}”` : "Publish draft dates") : confirm.kind === "delete" ? "Delete this draft date" : "Cancel this date"}
          confirmLabel={confirm.kind === "publish" ? "Publish" : confirm.kind === "delete" ? "Delete" : "Cancel date"}
          message={confirm.kind === "publish" ? `${drafts.length} future draft date(s) become visible to providers.${test.status === "DRAFT" ? " The test's subjects, duration and marking can no longer change." : ""}`
            : confirm.kind === "delete" ? "Providers never saw this date." : "Providers can no longer submit papers for it; papers already submitted stay as history."}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          {confirm.kind === "cancel" && <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />}
        </ConfirmDialog>
      )}
    </Card>
  );
}

function monthsFrom(start: string, count: number) {
  const [y, m] = start.split("-").map(Number);
  return Array.from({ length: count }, (_, i) => { const d = new Date(y, m - 1 + i, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; });
}

function zones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const all = new Set([...(intl.supportedValuesOf ? intl.supportedValuesOf("timeZone") : []), "Asia/Kolkata", "UTC"]);
  all.delete(local);
  return [local, ...[...all].sort()];
}

function PlanDatesDialog({ test, onClose, onDone }: { test: GlobalTest; onClose: () => void; onDone: (created: number, existing: number) => void }) {
  const next = new Date(); next.setMonth(next.getMonth() + 1, 1);
  const [f, setF] = useState({ from: `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}`, months: "3", weekdays: [5] as number[], time: "10:00",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, window: "180" });
  const { busy, error, run } = useAction();
  const months = monthsFrom(f.from, Math.min(Math.max(Number(f.months) || 1, 1), 12));
  const toggle = (d: number) => setF((x) => ({ ...x, weekdays: x.weekdays.includes(d) ? x.weekdays.filter((y) => y !== d) : [...x.weekdays, d] }));
  return (
    <Modal title={`Plan dates: ${test.name}`} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <div className="grid cols-3">
        <Field label="First month" type="month" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} />
        <Field label="Number of months" type="number" min={1} max={12} value={f.months} onChange={(e) => setF({ ...f, months: e.target.value })} />
        <Field label="Opens at (local time)" type="time" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} />
      </div>
      <fieldset><legend>Days of the week</legend>
        <div className="check-grid">{DAYS.map((d, i) => <label key={d}><input type="checkbox" checked={f.weekdays.includes(i)} onChange={() => toggle(i)} /> {d}</label>)}</div>
      </fieldset>
      <div className="grid cols-2">
        <SelectField label="Time zone" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })}>{zones().map((z) => <option key={z}>{z}</option>)}</SelectField>
        <Field label="Open for (minutes)" type="number" min={test.duration_minutes} max={10080} value={f.window} onChange={(e) => setF({ ...f, window: e.target.value })}
          hint={`Students who start in time get the full ${test.duration_minutes} minutes.`} />
      </div>
      <p className="muted small">Creates draft dates for {months.join(", ")}. Dates already planned are kept; past dates are skipped.</p>
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={f.weekdays.length === 0 || !(Number(f.window) > 0)} onClick={() => void run(async () => {
          const r = await api<{ created: number; existing: number }>(`/api/v1/global-tests/${test.test_id}/occurrences/plan`, { method: "POST",
            body: { months, weekdays: [...f.weekdays].sort(), start_time: f.time, timezone: f.timezone, window_minutes: Number(f.window) } });
          onDone(r.created, r.existing);
        })}>Plan dates</Button>
      </div>
    </Modal>
  );
}

function defaultStart() {
  const d = new Date(Date.now() + 7 * 86400000);
  d.setMinutes(0, 0, 0);
  return d;
}

const toLocal = (v: string) => { const d = new Date(v); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };

function DateDialog({ test, occurrence, onClose, onDone }: { test: GlobalTest; occurrence: Occurrence | null; onClose: () => void; onDone: () => void }) {
  const [start, setStart] = useState(() => (occurrence ? toLocal(occurrence.start_at) : toLocal(defaultStart().toISOString())));
  const [end, setEnd] = useState(() => (occurrence ? toLocal(occurrence.end_at) : toLocal(new Date(defaultStart().getTime() + 3 * 3600000).toISOString())));
  const { busy, error, run } = useAction();
  return (
    <Modal title={occurrence ? "Change draft date" : `Add a date: ${test.name}`} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <div className="grid cols-2">
        <Field label="Opens" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        <Field label="Closes (last time to start)" type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
      </div>
      <p className="muted small">Times are in your time zone ({Intl.DateTimeFormat().resolvedOptions().timeZone}).</p>
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!start || !end || new Date(end) <= new Date(start)} onClick={() => void run(async () => {
          const body = { start_at: new Date(start).toISOString(), end_at: new Date(end).toISOString() };
          await api(occurrence ? `/api/v1/global-occurrences/${occurrence.occurrence_id}` : `/api/v1/global-tests/${test.test_id}/occurrences`,
            { method: occurrence ? "PUT" : "POST", body });
          onDone();
        })}>Save draft date</Button>
      </div>
    </Modal>
  );
}

/** One published date: who submitted what (in the caller's scope), what is missing, submitting and withdrawing papers. */
export function GlobalOccurrencePage() {
  const { occurrenceId = "" } = useParams();
  const { me, can } = useAuth();
  const platform = isGlobalRole(me?.primary_role) && can("examplan.manage");
  const d = useApi<OccurrenceDetail>(`/api/v1/global-occurrences/${occurrenceId}`);
  const teacherOnly = me?.primary_role === "TEACHER";   // teachers use the exam pages; the layout API refuses them
  const layout = useApi<StudentLayouts>(d.data?.generated_at && !teacherOnly ? `/api/v1/global-occurrences/${occurrenceId}/student-subjects` : null);
  const [submitting, setSubmitting] = useState(false);
  const [withdraw, setWithdraw] = useState<Submission | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const act = useAction();
  if (d.loading && !d.data) return <Spinner />;
  if (d.error || !d.data) return <Alert kind="error">{d.error || "Date not found."}</Alert>;
  const o = d.data;
  const mySubjects = o.subjects.filter((s) => s.can_submit);
  return (
    <div className="stack">
      <PageHeader crumbs={[<Link key="g" to="/tests/global">Global Tests</Link>, <Link key="t" to={`/tests/global/${o.test_id}`}>{o.test_name}</Link>, fmtDate(o.start_at)]}
        title={`${o.test_name}: ${fmtDate(o.start_at)}`}
        subtitle={`Open until ${fmtDate(o.end_at)} · ${o.duration_minutes} min · ${o.marking.mode === "UNIFORM" ? `+${o.marking.correct} / −${o.marking.wrong}` : "each paper's own marks"}`}
        actions={<DateState o={o} />} />
      {notice && <Alert kind="success">{notice}</Alert>}
      {act.error && !withdraw && <Alert kind="error">{act.error}</Alert>}
      {o.status === "CANCELLED" && <Alert kind="warn">This date was cancelled: {o.cancel_reason}</Alert>}
      <Card>
        <div className="card-head"><h2>Papers</h2>
          {!platform && o.submissions_open && mySubjects.length > 0 && <Button onClick={() => setSubmitting(true)}>Submit a paper</Button>}</div>
        <p className="muted small">{o.submissions_open
          ? `Papers are accepted until ${fmtDate(o.submission_deadline)}. Each student receives the paper submitted for them; two papers of the same subject can never cover the same student.`
          : o.generated_at ? "The deadline has passed: every student received their own paper and the exams no longer change." : "Papers are no longer accepted for this date."}</p>
        <DataTable rows={o.submission_list} rowKey={(s) => s.submission_id}
          empty={<EmptyState title={platform ? "No papers submitted yet" : "You have not submitted a paper for this date"} />}
          columns={[
            { header: "Subject", render: (s) => s.subject },
            { header: "Paper", render: (s) => <>{s.paper_title} v{s.version_no}<div className="muted small">{s.question_count} questions</div></> },
            { header: "Approval", render: (s) => <>{<StatusBadge status={s.version_status ?? "?"} />}{!s.ready && s.status === "ACTIVE" && <div className="muted small">approve and publish it before the deadline</div>}</> },
            { header: "For", render: (s) => <>{audienceText(s.audience)}<div className="muted small"><StudentCount count={s.students_now} url={`/api/v1/global-paper-submissions/${s.submission_id}/students`}
              title={`Students “${s.paper_title}” reaches now`} context={`The students this ${s.subject} paper is for at present (its audience, subject and the test's class).`} /> student(s) now</div></> },
            { header: "From", render: (s) => <>{[s.provider_name, s.branch_name].filter(Boolean).join(" · ")}<div className="muted small">{s.submitted_by_name}</div></> },
            { header: "Status", render: (s) => <><StatusBadge status={s.status} />{s.withdraw_reason && <div className="muted small">{s.withdraw_reason}</div>}</> },
            { header: "", render: (s) => (s.can_withdraw && o.submissions_open ? <Button small variant="secondary" onClick={() => { setReason(""); setWithdraw(s); }}>Withdraw</Button> : null) },
          ]} />
      </Card>
      {o.coverage.length > 0 && (
        <Card>
          <h2>Coverage</h2>
          <DataTable rows={o.coverage} rowKey={(c) => c.provider_id + c.subject_id} columns={[
            ...(platform ? [{ header: "Provider", render: (c: OccurrenceDetail["coverage"][number]) => c.provider_name }] : []),
            { header: "Subject", render: (c) => c.subject },
            { header: "Papers", render: (c) => `${c.submissions} submitted · ${c.ready} approved` },
            { header: "Students covered now", render: (c) => <StudentCount count={c.students_covered}
              url={`/api/v1/global-occurrences/${o.occurrence_id}/coverage/students?provider_id=${c.provider_id}&subject_id=${c.subject_id}`}
              title={`${c.subject}: students covered by ${c.provider_name}`} context="The students who would receive this provider's approved paper for the subject if the exams were generated now." /> },
            { header: "", render: (c) => (c.missing ? <Badge tone="danger">Missing paper</Badge> : c.ambiguous ? <Badge tone="warn">{c.ambiguous} covered twice</Badge> : <Badge tone="ok">Covered</Badge>) },
          ]} />
        </Card>
      )}
      {o.exams && (
        <Card>
          <h2>Generated exams</h2>
          <DataTable rows={o.exams} rowKey={(e) => e.exam_id} empty={<EmptyState title="No exam in your scope" />} columns={[
            { header: "Exam", render: (e) => <Link to={`/exams/${e.exam_id}`}>{e.name}</Link> }, { header: "Status", render: (e) => <StatusBadge status={e.status} /> },
            { header: "Assigned students", render: (e) => <StudentCount count={e.assigned} url={`/api/v1/exams/${e.exam_id}/assignments`} title={`Students assigned to ${e.name}`} context="The students this exam is assigned to that you may see, with their attempt status." /> },
          ]} />
        </Card>
      )}
      {layout.data && (
        <Card>
          <h2>Students</h2>
          <p className="muted small">{layout.data.scope === "ALL"
            ? "Every eligible student has one Global Test exam with every configured subject: their own paper, or the reason they have none."
            : "Your students and only your institute's part of their exam: the papers you provided, or why your institute provided none."}</p>
          <DataTable rows={layout.data.students} rowKey={(st) => st.student_id} empty={<EmptyState title="No student in your scope" />} columns={[
            { header: "Student", render: (st) => <>{st.name ?? st.student_id}<div className="muted small">{st.student_id}</div></> },
            ...layout.data.subjects.map((subj) => ({ header: subj.subject, render: (st: StudentLayouts["students"][number]) => {
              const c = st.subjects.find((x) => x.subject_id === subj.subject_id);
              if (!c) return <span className="muted">—</span>;
              return c.status === "PAPER" ? <Badge tone="ok">{c.provider_name ?? "Paper"}</Badge>
                : <span title={c.message ?? ""}><Badge tone={c.status === "CONFLICT" ? "danger" : "warn"}>{LAYOUT_LABELS[c.status] ?? c.status}</Badge></span>;
            } })),
            { header: "Exam", render: (st) => (st.exam_id ? <>{st.exam_status ? <StatusBadge status={st.exam_status} /> : null}</> : <span className="muted">none</span>) },
          ]} />
        </Card>
      )}
      {submitting && <SubmitPaperDialog o={o} onClose={() => setSubmitting(false)} onDone={(s) => { setSubmitting(false); setNotice(`Submitted “${s.paper_title}” for ${s.students_now} student(s).`); d.reload(); }} />}
      {withdraw && (
        <ConfirmDialog title={`Withdraw “${withdraw.paper_title}”`} danger confirmLabel="Withdraw" busy={act.busy} onCancel={() => setWithdraw(null)}
          message="Its students will have no paper for this subject unless another paper is submitted for them before the deadline."
          onConfirm={() => void act.run(async () => {
            await api(`/api/v1/global-paper-submissions/${withdraw.submission_id}/withdraw`, { method: "POST", body: { reason } });
            setWithdraw(null); setNotice("Submission withdrawn."); d.reload();
          })}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        </ConfirmDialog>
      )}
    </div>
  );
}

function SubmitPaperDialog({ o, onClose, onDone }: { o: OccurrenceDetail; onClose: () => void; onDone: (s: Submission) => void }) {
  const scope = useScope();
  const providerId = scope.providerId;
  const subjects = o.subjects.filter((s) => s.can_submit);
  const [subject, setSubject] = useState(subjects[0]?.subject_id ?? "");
  const [paper, setPaper] = useState("");
  const [aud, setAud] = useState<AudienceState>(emptyAudience);
  const [errors, setErrors] = useState<CheckError[]>([]);
  const [request, setRequest] = useState<{ key: string; body: string } | null>(null);
  const { busy, error, run } = useAction();
  const sc = useApi<ScopeResponse>(providerId ? `/api/v1/global-occurrences/${o.occurrence_id}/scope?provider_id=${providerId}` : null);
  const papers = useApi<{ items: SubmittablePaper[] }>(providerId && subject ? `/api/v1/global-occurrences/${o.occurrence_id}/papers?provider_id=${providerId}&subject_id=${subject}` : null);
  const branches = (sc.data?.branches ?? []).map((b) => ({ ...b, subjects: b.subjects.filter((s) => s.subject_id === subject) })).filter((b) => b.subjects.length);
  const scopeForSubject = sc.data ? { ...sc.data, branches } : null;
  const chosen = papers.data?.items.find((p) => p.paper_version_id === paper);
  const incomplete = !subject ? "Choose a subject." : !paper ? "Choose your paper." : audienceReady(aud);
  return (
    <Modal title={`Submit a paper: ${o.test_name}, ${fmtDate(o.start_at)}`} onClose={onClose}>
      <ScopeBar branch={false} />
      {error && <Alert kind="error">{error}</Alert>}
      {errors.length > 0 && <Alert kind="error"><ul className="plain">{errors.map((e, i) => <li key={i}>{e.message}</li>)}</ul></Alert>}
      <SelectField label="Subject" value={subject} onChange={(e) => { setSubject(e.target.value); setPaper(""); setAud(emptyAudience()); }}>
        {subjects.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.subject}</option>)}
      </SelectField>
      {papers.loading && !papers.data ? <Spinner /> : (papers.data?.items.length ?? 0) === 0 ? (
        <EmptyState title="No paper of yours for this subject" hint="Upload it first (file or photos) in Question Bank & Papers, then come back." action={<Link className="btn small" to="/papers">Upload a paper</Link>} />
      ) : (
        <SelectField label="Your paper" value={paper} onChange={(e) => setPaper(e.target.value)}>
          <option value="">Choose a paper</option>
          {papers.data!.items.map((p) => <option key={p.paper_version_id} value={p.paper_version_id}>{p.title} v{p.version_no} · {p.question_count} questions · {p.version_status.toLowerCase()}</option>)}
        </SelectField>
      )}
      {chosen && !chosen.ready && <Alert kind="warn">This version is not approved yet. It is used only if it is approved and published before {fmtDate(o.submission_deadline)}.</Alert>}
      <h3>For which students?</h3>
      {sc.loading && !sc.data ? <Spinner /> : !scopeForSubject || scopeForSubject.branches.length === 0
        ? <EmptyState title="No students you may assign for this subject" />
        : <AudienceFields providerId={providerId} scope={scopeForSubject} value={aud} onChange={setAud} studentsPath={`/api/v1/global-occurrences/${o.occurrence_id}/students`} />}
      <div className="modal-actions">
        {incomplete && <span className="muted small">{incomplete}</span>}
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!!incomplete} onClick={() => void run(async () => {
          setErrors([]);
          const body = { subject_id: subject, paper_version_id: paper, audience: audienceBody(providerId, aud.branchIds, aud.wholeIds, aud.batchIds, [...aud.students.keys()]) };
          const json = JSON.stringify(body);
          const key = request && request.body === json ? request.key : newRequestKey();      // a retry of the SAME request reuses its key
          setRequest({ key, body: json });
          try {
            onDone(await api<Submission>(`/api/v1/global-occurrences/${o.occurrence_id}/submissions`, { method: "POST", body, headers: { "Idempotency-Key": key } }));
          } catch (e) {
            if (e instanceof ApiError && Array.isArray(e.extra.errors)) { setErrors(e.extra.errors as CheckError[]); throw new Error(e.message, { cause: e }); }
            throw e;
          }
        })}>Submit paper</Button>
      </div>
    </Modal>
  );
}

/** Definition card of a SCHEDULE test (used by the test page). */
export function GlobalTestDefinition({ test }: { test: GlobalTest }) {
  return (
    <dl className="summary-grid">
      <dt>Subjects</dt><dd>{subjectsOf(test)}</dd>
      <dt>Duration</dt><dd>{test.duration_minutes} minutes</dd>
      <dt>Marking</dt><dd>{markingOf(test)}</dd>
      <dt>Exam / class</dt><dd>{[test.exam_type, test.target_class].filter(Boolean).join(" · ") || "—"}</dd>
      <dt>Paper deadline</dt><dd>{test.generate_lead_minutes / 60} hour(s) before each date opens</dd>
      <dt>Papers</dt><dd>Each provider submits its own paper per subject, date and audience</dd>
    </dl>
  );
}
