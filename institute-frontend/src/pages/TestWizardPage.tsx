import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { isGlobalRole } from "../auth/roles";
import NeedBranch from "../components/NeedBranch";
import type { GlobalTest } from "../components/globalTests";
import { QuestionBrowser } from "../components/questionBank";
import { Alert, Button, Card, DataTable, EmptyState, Field, PageHeader, SearchField, SelectField, Spinner, StatusBadge } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api, errorMessage, type Page } from "../services/api";
import { buildPapers, groupBySubject, type BankQuestion } from "../services/questions";

type Subject = { subject_id: string; name: string };
type Batch = { batch_id: string; name: string; status: string };
type PV = { paper_version_id: string; version_no: number; status: string; question_count: number; total_marks: string };
type PaperRow = { paper_id: string; scope: string; subject_id: string; title: string; latest_version: PV | null };
type DirStudent = { student_id: string; name: string | null };
type Audience = { audience_type: "BATCHES" | "BRANCH" | "INSTITUTE" | "STUDENTS"; batch_ids: string[]; student_ids: string[] };
type Preview = { eligible_count: number; sample_student_ids: string[]; not_eligible_student_ids: string[] };
type Plan = { plan_id: string; name: string; status: string };
type Job = { job_id: string; status: string; exam_id: string | null; error: string | null };
type Slot = { subject_id: string; paper_version_id: string; label: string; question_count: number; total_marks: number; status: string };

const STEPS = [
  { id: "test", label: "Choose test" },
  { id: "audience", label: "Audience" },
  { id: "source", label: "Question source" },
  { id: "configure", label: "Configure" },
  { id: "review", label: "Review & schedule" },
] as const;
type Step = (typeof STEPS)[number]["id"];

const toLocalInput = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
const fmt = (s: string) => (s ? new Date(s).toLocaleString() : "—");
const AUDIENCE_LABEL = { STUDENTS: "Individual students", BATCHES: "One or more batches", BRANCH: "The whole branch", INSTITUTE: "The whole institute (every branch)" };

/** Create a test: Choose test -> Assign audience -> Choose question source -> Configure -> Review -> Schedule/Publish.
 * Every step is backed by the real API: papers are built from bank questions (referenced, never copied), the exam plan pins exact
 * paper versions, audiences are resolved and checked by the server, and a Global Daily Test is scheduled without being modified. */
export default function TestWizardPage() {
  return (
    <>
      <PageHeader crumbs={[<Link key="plans" to="/exam-plans">Exam Plans</Link>, "Create a test"]} title="Create a test"
        subtitle="Choose the test, who takes it, where its questions come from, its rules, then review and schedule it." />
      <NeedBranch>{({ providerId, branchId }) => <Wizard key={branchId} providerId={providerId} branchId={branchId} />}</NeedBranch>
    </>
  );
}

function Wizard({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { me, can } = useAuth();
  const scope = useScope();
  const [params] = useSearchParams();
  const [step, setStep] = useState<Step>(params.get("global") ? "audience" : "test");
  const [kind, setKind] = useState<"new" | "global">(params.get("global") ? "global" : "new");
  const [globalId, setGlobalId] = useState(params.get("global") ?? "");
  const [audience, setAudience] = useState<Audience>({ audience_type: "BATCHES", batch_ids: [], student_ids: [] });
  const [sourceMode, setSourceMode] = useState<"bank" | "papers">("bank");
  const [selected, setSelected] = useState<Map<string, BankQuestion>>(new Map());
  const [slots, setSlots] = useState<Record<string, Slot>>({});
  const [cfg, setCfg] = useState(() => {
    const start = new Date(Date.now() + 86400000);
    start.setMinutes(0, 0, 0);
    return { name: "", exam_type: "", target_class: "", duration: "180", marking: "PER_QUESTION" as "PER_QUESTION" | "UNIFORM", correct: "4", wrong: "1",
      start: toLocalInput(start), end: toLocalInput(new Date(start.getTime() + 6 * 3600000)), generateAt: "", autoPublish: true };
  });
  const [publishPapers, setPublishPapers] = useState(true);
  const [result, setResult] = useState<{ plan: Plan; job: Job | null } | null>(null);
  const submit = useAction();

  const globalTest = useApi<GlobalTest>(kind === "global" && globalId ? `/api/v1/global-tests/${globalId}` : null);
  const gt = globalTest.data;
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const sName = (id: string) => subjects.data?.find((s) => s.subject_id === id)?.name ?? "Subject";
  const canPublishPapers = can("paper.approve") && can("paper.publish");
  const providerLevel = isGlobalRole(me?.primary_role) || me?.primary_role === "PROVIDER_ADMIN";

  // A Global Daily Test fixes duration, exam type, class and marking; its name is the default test name.
  const locked = kind === "global" && gt ? { duration: String(gt.duration_minutes), exam_type: gt.exam_type ?? "", target_class: gt.target_class ?? "", marking: "PER_QUESTION" as const } : null;
  const eff = { ...cfg, ...(locked ?? {}), name: cfg.name || (kind === "global" ? gt?.name ?? "" : "") };

  const steps = STEPS.filter((s) => kind === "new" || s.id !== "source");
  const index = steps.findIndex((s) => s.id === step);
  const go = (delta: number) => setStep(steps[Math.min(Math.max(index + delta, 0), steps.length - 1)].id);

  // what the exam will contain
  const bankGroups = groupBySubject(selected);
  const sections: { subject: string; questions: number; marks: number; detail: string }[] = kind === "global" && gt
    ? gt.sections.map((s) => ({ subject: s.subject ?? "Subject", questions: s.question_count, marks: Number(s.total_marks), detail: `${s.paper_title} v${s.version_no}` }))
    : sourceMode === "bank"
      ? bankGroups.map((g) => ({ subject: sName(g.subject_id), questions: g.question_ids.length,
        marks: g.question_ids.reduce((m, id) => m + Number(selected.get(id)!.latest.marks_positive), 0), detail: "new paper from selected bank questions" }))
      : Object.values(slots).map((s) => ({ subject: sName(s.subject_id), questions: s.question_count, marks: s.total_marks, detail: s.label }));
  const questionCount = sections.reduce((n, s) => n + s.questions, 0);
  const totalMarks = eff.marking === "UNIFORM" ? questionCount * Number(eff.correct || 0) : sections.reduce((n, s) => n + s.marks, 0);

  const preview = useAudiencePreview(branchId, audience);
  const valid: Record<Step, string | null> = {
    test: kind === "global" && !gt ? "Choose a published Global Daily Test." : null,
    audience: audience.audience_type === "BATCHES" && audience.batch_ids.length === 0 ? "Choose at least one batch."
      : audience.audience_type === "STUDENTS" && audience.student_ids.length === 0 ? "Choose at least one student."
        : preview.data?.not_eligible_student_ids.length ? "Remove the students who are not actively enrolled with this provider." : null,
    source: kind === "new" && sections.length === 0 ? "Select questions or papers for at least one subject." : null,
    configure: eff.name.trim().length < 3 ? "Give the test a name."
      : !(Number(eff.duration) > 0 && Number(eff.duration) <= 720) ? "Duration must be between 1 and 720 minutes."
        : new Date(eff.end) <= new Date(eff.start) ? "The window must end after it starts."
          : eff.generateAt && new Date(eff.generateAt) > new Date(eff.start) ? "Generation must happen before the window opens."
            : eff.marking === "UNIFORM" && !(/^\d+$/.test(eff.correct) && Number(eff.correct) > 0 && /^\d+$/.test(eff.wrong)) ? "Marks must be whole numbers; correct > 0." : null,
    review: null,
  };

  const save = (mode: "draft" | "schedule" | "now") => submit.run(async () => {
    const window = { start_at: new Date(eff.start).toISOString(), end_at: new Date(eff.end).toISOString(),
      generate_at: eff.generateAt ? new Date(eff.generateAt).toISOString() : null, auto_publish: eff.autoPublish, status: mode === "draft" ? "DRAFT" : "ACTIVE" };
    let plan: Plan;
    if (kind === "global") {
      plan = await api<Plan>(`/api/v1/global-tests/${globalId}/schedule`, { method: "POST", body: { branch_id: branchId, name: eff.name.trim(), ...audience, ...window } });
    } else {
      const planBody = {
        name: eff.name.trim(), duration_minutes: Number(eff.duration), exam_type: eff.exam_type.trim() || null, target_class: eff.target_class.trim() || null,
        marking_mode: eff.marking, ...(eff.marking === "UNIFORM" ? { marks_correct: Number(eff.correct), marks_wrong: Number(eff.wrong) } : {}),
        ...audience, ...window };
      let planSlots: { subject_id: string; paper_version_id: string }[];
      if (sourceMode === "bank") {
        // Nothing is created until everything is known to be valid: first the plan itself (subjects still to be built), then
        // every paper as a dry run. Only then are the papers built (and approved/published), and the plan created from them.
        const groups = bankGroups.map((g) => ({ ...g, title: `${eff.name.trim()} – ${sName(g.subject_id)}` }));
        await api(`/api/v1/branches/${branchId}/exam-plans/check`, { method: "POST", body: { ...planBody, pending_subject_ids: groups.map((g) => g.subject_id) } });
        for (const g of groups) {
          await api(`/api/v1/papers/check`, { method: "POST", body: { scope: "PROVIDER", branch_id: branchId, subject_id: g.subject_id, title: g.title,
            question_ids: g.question_ids, exam_type: eff.exam_type.trim() || null, target_class: eff.target_class.trim() || null } });
        }
        const made: string[] = [];
        try {
          const built = await buildPapers("PROVIDER", branchId, groups, { exam_type: eff.exam_type, target_class: eff.target_class },
            (b) => made.push(`${sName(b.subject_id)} (v${b.version_no})`));
          if (publishPapers && canPublishPapers && mode !== "draft") {
            for (const b of built) {   // the normal paper workflow, explicitly, by someone allowed to do it
              await api(`/api/v1/paper-versions/${b.paper_version_id}/approve`, { method: "POST", body: { note: `Approved while creating “${eff.name.trim()}”` } });
              await api(`/api/v1/paper-versions/${b.paper_version_id}/publish`, { method: "POST", body: { note: null } });
            }
          }
          planSlots = built.map((b) => ({ subject_id: b.subject_id, paper_version_id: b.paper_version_id }));
          plan = await api<Plan>(`/api/v1/branches/${branchId}/exam-plans`, { method: "POST", body: { ...planBody, slots: planSlots } });
        } catch (e) {
          // Only possible if something changed between the checks and now; say exactly what already exists (nothing is hidden).
          if (made.length) throw new Error(`${errorMessage(e)} The test was not created. These papers were already built and are listed under Papers: ${made.join(", ")}.`, { cause: e });
          throw e;
        }
      } else {
        planSlots = Object.values(slots).map((s) => ({ subject_id: s.subject_id, paper_version_id: s.paper_version_id }));
        plan = await api<Plan>(`/api/v1/branches/${branchId}/exam-plans`, { method: "POST", body: { ...planBody, slots: planSlots } });
      }
    }
    let job: Job | null = null;
    if (mode === "now") {
      job = await api<Job>(`/api/v1/exam-plans/${plan.plan_id}/generate`, { method: "POST", body: {}, headers: { "Idempotency-Key": `wizard-${plan.plan_id}` } });
    }
    setResult({ plan, job });
  });

  if (result) {
    return (
      <Card>
        <h2>{result.plan.status === "DRAFT" ? "Draft saved" : result.job ? "Test generated" : "Test scheduled"}</h2>
        {result.plan.status === "DRAFT" && <p>The plan “{result.plan.name}” is saved as a draft. It generates nothing until you activate it on the Exam Plans page.</p>}
        {result.plan.status !== "DRAFT" && !result.job && <p>“{result.plan.name}” is scheduled.{eff.generateAt ? ` It is generated automatically at ${fmt(eff.generateAt)}.` : " Generate it from the Exam Plans page when you are ready."}</p>}
        {result.job && (
          <Alert kind={result.job.status === "FAILED" ? "error" : result.job.status === "PARTIAL" ? "warn" : "success"}>
            Generation {result.job.status.toLowerCase()}{result.job.error ? `: ${result.job.error}` : ""}.
            {result.job.status === "PARTIAL" && " Some subjects had no approved paper; they were left out and the responsible staff were notified."}
            {result.job.exam_id && <> <Link to={`/exams/${result.job.exam_id}`}>Open the exam</Link></>}
          </Alert>
        )}
        <div className="row"><Link className="btn secondary" to="/exam-plans">Back to Exam Plans</Link></div>
      </Card>
    );
  }

  return (
    <div className="wizard">
      <ol className="wizard-steps" aria-label="Steps">
        {steps.map((s, i) => (
          <li key={s.id} aria-current={s.id === step ? "step" : undefined} className={i < index ? "done" : undefined}>
            <span className="wizard-step-no">{i + 1}</span> {s.label}
          </li>
        ))}
      </ol>

      {step === "test" && (
        <Card>
          <h2>What do you want to conduct?</h2>
          <div className="choice-grid" role="radiogroup" aria-label="Test type">
            <label className={`choice ${kind === "new" ? "selected" : ""}`}>
              <input type="radio" name="kind" checked={kind === "new"} onChange={() => setKind("new")} />
              <strong>Create a new test</strong>
              <span className="muted small">One subject, several subjects or a complete exam, from the question bank or from papers.</span>
            </label>
            <label className={`choice ${kind === "global" ? "selected" : ""}`}>
              <input type="radio" name="kind" checked={kind === "global"} onChange={() => setKind("global")} />
              <strong>Global Daily Test</strong>
              <span className="muted small">A test published by the platform, assigned exactly as published.</span>
            </label>
          </div>
          {kind === "global" && <GlobalPicker value={globalId} onChange={setGlobalId} />}
        </Card>
      )}

      {step === "audience" && (
        <AudienceStep providerId={providerId} branchId={branchId} branchName={scope.branch?.name} audience={audience} onChange={setAudience}
          allowInstitute={providerLevel} preview={preview} />
      )}

      {step === "source" && kind === "new" && (
        <Card>
          <h2>Where do the questions come from?</h2>
          <div className="choice-grid" role="radiogroup" aria-label="Question source">
            <label className={`choice ${sourceMode === "bank" ? "selected" : ""}`}>
              <input type="radio" name="source" checked={sourceMode === "bank"} onChange={() => setSourceMode("bank")} />
              <strong>Question bank</strong><span className="muted small">Filter by exam type, class, subject, chapter, topic and difficulty and pick questions.</span>
            </label>
            <label className={`choice ${sourceMode === "papers" ? "selected" : ""}`}>
              <input type="radio" name="source" checked={sourceMode === "papers"} onChange={() => setSourceMode("papers")} />
              <strong>Papers & test creation</strong><span className="muted small">Use an existing paper per subject, or upload a question file and answer key.</span>
            </label>
          </div>
          {sourceMode === "bank" ? (
            <>
              <p className="muted small">Questions from this branch and the platform bank. Selected questions become one new paper per subject when you schedule.</p>
              {selected.size > 0 && <p><strong>{selected.size}</strong> selected in {bankGroups.length} subject(s). <Button small variant="secondary" onClick={() => setSelected(new Map())}>Clear</Button></p>}
              <QuestionBrowser source="ALL" branchId={branchId} selectable selected={selected} onToggle={(q) => setSelected((cur) => {
                const next = new Map(cur);
                if (next.has(q.question_id)) next.delete(q.question_id); else next.set(q.question_id, q);
                return next;
              })} />
            </>
          ) : (
            <PaperSlots providerId={providerId} branchId={branchId} subjects={subjects.data ?? []} slots={slots} onChange={setSlots} />
          )}
        </Card>
      )}

      {step === "configure" && (
        <Card>
          <h2>Configure the examination</h2>
          {kind === "global" && <Alert kind="info">A Global Daily Test keeps its own papers, duration ({gt?.duration_minutes} min) and per-question marking. You choose its name, audience and dates.</Alert>}
          <div className="grid cols-3">
            <Field label="Test name" value={eff.name} onChange={(e) => setCfg({ ...cfg, name: e.target.value })} />
            <Field label="Exam type" value={eff.exam_type} disabled={kind === "global"} onChange={(e) => setCfg({ ...cfg, exam_type: e.target.value })} placeholder="NEET" />
            <Field label="Class / level" value={eff.target_class} disabled={kind === "global"} onChange={(e) => setCfg({ ...cfg, target_class: e.target.value })} placeholder="Class 12" />
            <Field label="Duration (minutes, one shared timer)" type="number" min={1} max={720} value={eff.duration} disabled={kind === "global"} onChange={(e) => setCfg({ ...cfg, duration: e.target.value })} />
            <Field label="Window opens (students may start from)" type="datetime-local" value={eff.start} onChange={(e) => setCfg({ ...cfg, start: e.target.value })} />
            <Field label="Window closes (last time to start)" type="datetime-local" value={eff.end} onChange={(e) => setCfg({ ...cfg, end: e.target.value })} />
            <Field label="Generate automatically at (optional)" type="datetime-local" value={eff.generateAt} onChange={(e) => setCfg({ ...cfg, generateAt: e.target.value })}
              hint="Leave empty to generate it yourself (or choose “Schedule & generate now” on the review step)." />
          </div>
          <label className="row checkbox-field"><input type="checkbox" checked={eff.autoPublish} onChange={(e) => setCfg({ ...cfg, autoPublish: e.target.checked })} /> Publish to students automatically once generated</label>
          <h3>Marking scheme</h3>
          <div className="grid cols-3">
            <SelectField label="Marks" value={eff.marking} disabled={kind === "global"} onChange={(e) => setCfg({ ...cfg, marking: e.target.value as "PER_QUESTION" | "UNIFORM" })}>
              <option value="PER_QUESTION">Use each question's own marks</option><option value="UNIFORM">Same marks for every question</option>
            </SelectField>
            {eff.marking === "UNIFORM" && <>
              <Field label="Marks for a correct answer" inputMode="numeric" value={eff.correct} onChange={(e) => setCfg({ ...cfg, correct: e.target.value })} />
              <Field label="Marks deducted for a wrong answer" inputMode="numeric" value={eff.wrong} onChange={(e) => setCfg({ ...cfg, wrong: e.target.value })} hint="0 for no negative marking" />
            </>}
          </div>
          <FixedPolicies />
          {valid.configure && <Alert kind="warn">{valid.configure}</Alert>}
        </Card>
      )}

      {step === "review" && (
        <Card>
          <h2>Review</h2>
          <dl className="facts">
            <dt>Test</dt><dd>{kind === "global" ? <>Global Daily Test “{gt?.name}” <StatusBadge status="PUBLISHED" /></> : eff.name}</dd>
            <dt>Name students see</dt><dd>{eff.name}</dd>
            <dt>Question source</dt><dd>{kind === "global" ? "The published test's platform papers (unchanged)" : sourceMode === "bank" ? `Question bank: ${selected.size} selected questions` : "Existing / uploaded papers"}</dd>
            <dt>Questions</dt><dd>{questionCount} in {sections.length} subject(s)</dd>
            <dt>Total marks</dt><dd>{totalMarks}</dd>
            <dt>Marking</dt><dd>{eff.marking === "UNIFORM" ? `+${eff.correct} correct, −${eff.wrong} wrong, 0 unanswered` : "Each question's own marks (negative marks as set on the question), 0 unanswered"}</dd>
            <dt>Duration</dt><dd>{eff.duration} minutes, one timer across all subjects</dd>
            <dt>Audience</dt><dd>{AUDIENCE_LABEL[audience.audience_type]}{audience.audience_type === "BATCHES" ? ` (${audience.batch_ids.length})` : audience.audience_type === "STUDENTS" ? ` (${audience.student_ids.length})` : ""}
              {" · "}<strong>{preview.data ? `${preview.data.eligible_count} eligible student(s) now` : preview.loading ? "checking…" : "—"}</strong></dd>
            <dt>Window</dt><dd>{fmt(eff.start)} → {fmt(eff.end)}</dd>
            <dt>Generation</dt><dd>{eff.generateAt ? `Automatically at ${fmt(eff.generateAt)}` : "When you generate it"} · {eff.autoPublish ? "published to students automatically" : "you publish it after generation"}</dd>
            <dt>Attempts & results</dt><dd>One attempt with resume; results after evaluation; answers reviewable after the window closes.</dd>
          </dl>
          <DataTable rows={sections} rowKey={(s) => s.subject + s.detail} columns={[
            { header: "Subject", render: (s) => s.subject }, { header: "Source", render: (s) => s.detail },
            { header: "Questions", render: (s) => s.questions }, { header: "Marks", render: (s) => (eff.marking === "UNIFORM" ? s.questions * Number(eff.correct || 0) : s.marks) },
          ]} />
          {kind === "new" && sourceMode === "bank" && (canPublishPapers ? (
            <label className="row checkbox-field"><input type="checkbox" checked={publishPapers} onChange={(e) => setPublishPapers(e.target.checked)} />
              Approve and publish the new paper(s) now (otherwise they wait for approval and the exam is generated without them)</label>
          ) : <Alert kind="info">The new papers will need approval and publication by someone permitted; until then generation reports those subjects as missing.</Alert>)}
          {kind === "new" && sourceMode === "papers" && Object.values(slots).some((s) => !["APPROVED", "PUBLISHED"].includes(s.status)) && (
            <Alert kind="warn">Some chosen paper versions are not approved yet. Generation will leave those subjects out and notify the responsible staff.</Alert>
          )}
          {submit.error && <Alert kind="error">{submit.error}</Alert>}
          <div className="row wizard-submit">
            <Button variant="secondary" busy={submit.busy} onClick={() => void save("draft")}>Save as draft</Button>
            <Button busy={submit.busy} onClick={() => void save("schedule")}>Schedule</Button>
            {can("exam.generate") && <Button busy={submit.busy} onClick={() => void save("now")}>Schedule & generate now</Button>}
          </div>
        </Card>
      )}

      <div className="row between wizard-nav">
        <Button variant="secondary" disabled={index === 0} onClick={() => go(-1)}>Back</Button>
        {step !== "review" && (
          <span className="row">
            {valid[step] && <span className="muted small" role="status">{valid[step]}</span>}
            <Button disabled={!!valid[step]} onClick={() => go(1)}>Next</Button>
          </span>
        )}
      </div>
    </div>
  );
}

function FixedPolicies() {
  return (
    <details className="small" open>
      <summary>Rules every exam follows</summary>
      <ul>
        <li>One attempt per student; a disconnected student resumes the same attempt.</li>
        <li>Starting inside the window gives the full duration, even if it runs past the window's end. Students who never start are marked missed.</li>
        <li>Unanswered questions score 0.</li>
        <li>Results appear after evaluation; correct answers and explanations become visible only after the window closes.</li>
        <li>Questions appear in paper order (shuffling is not available).</li>
      </ul>
    </details>
  );
}

function GlobalPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const list = useApi<Page<GlobalTest>>("/api/v1/global-tests?status=PUBLISHED&page_size=50");
  if (list.loading && !list.data) return <Spinner />;
  if (list.error) return <Alert kind="error">{list.error}</Alert>;
  const items = (list.data?.items ?? []).filter((t) => t.status === "PUBLISHED");
  if (items.length === 0) return <EmptyState title="No published Global Daily Tests" />;
  return (
    <div className="choice-grid" role="radiogroup" aria-label="Global Daily Test">
      {items.map((t) => (
        <label key={t.test_id} className={`choice ${value === t.test_id ? "selected" : ""}`}>
          <input type="radio" name="global" checked={value === t.test_id} onChange={() => onChange(t.test_id)} />
          <strong>{t.name}</strong>
          <span className="small">{[t.exam_type, t.target_class].filter(Boolean).join(" · ")}</span>
          <span className="muted small">{t.sections.map((s) => s.subject).join(", ")} · {t.question_count} questions · {Number(t.total_marks)} marks · {t.duration_minutes} min</span>
          {t.published_at && <span className="muted small">Published {new Date(t.published_at).toLocaleDateString()}{t.publisher ? ` by ${t.publisher}` : ""}</span>}
        </label>
      ))}
    </div>
  );
}

const EMPTY_PREVIEW: Preview = { eligible_count: 0, sample_student_ids: [], not_eligible_student_ids: [] };

function useAudiencePreview(branchId: string, audience: Audience) {
  const [result, setResult] = useState<{ key: string; data: Preview | null; error: string } | null>(null);
  const key = `${branchId}|${JSON.stringify(audience)}`;
  const empty = (audience.audience_type === "BATCHES" && !audience.batch_ids.length) || (audience.audience_type === "STUDENTS" && !audience.student_ids.length);
  useEffect(() => {
    if (empty) return;
    let cancelled = false;
    const body = JSON.parse(key.slice(key.indexOf("|") + 1)) as Audience;
    api<Preview>(`/api/v1/branches/${branchId}/audience-preview`, { method: "POST", body })
      .then((data) => { if (!cancelled) setResult({ key, data, error: "" }); })
      .catch((e) => { if (!cancelled) setResult({ key, data: null, error: e instanceof Error ? e.message : "Could not check the audience." }); });
    return () => { cancelled = true; };
  }, [branchId, key, empty]);
  if (empty) return { data: EMPTY_PREVIEW, loading: false, error: "" };
  const fresh = result?.key === key;
  return { data: fresh ? result.data : null, loading: !fresh, error: fresh ? result.error : "" };
}

function AudienceStep({ providerId, branchId, branchName, audience, onChange, allowInstitute, preview }: {
  providerId: string; branchId: string; branchName?: string; audience: Audience; onChange: (a: Audience) => void; allowInstitute: boolean;
  preview: { data: Preview | null; loading: boolean; error: string };
}) {
  const batches = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const set = (t: Audience["audience_type"]) => onChange({ audience_type: t, batch_ids: [], student_ids: [] });
  return (
    <Card>
      <h2>Who takes this test?</h2>
      <div className="choice-grid" role="radiogroup" aria-label="Audience">
        {(["STUDENTS", "BATCHES", "BRANCH", "INSTITUTE"] as const).filter((t) => t !== "INSTITUTE" || allowInstitute).map((t) => (
          <label key={t} className={`choice ${audience.audience_type === t ? "selected" : ""}`}>
            <input type="radio" name="audience" checked={audience.audience_type === t} onChange={() => set(t)} />
            <strong>{AUDIENCE_LABEL[t]}</strong>
            <span className="muted small">{t === "BRANCH" ? `Every student in an active batch of ${branchName ?? "this branch"}.` : t === "INSTITUTE" ? "Every student in an active batch of any branch of this provider." : t === "BATCHES" ? "Students in the batches you choose." : "Specific students enrolled with this provider."}</span>
          </label>
        ))}
      </div>
      {audience.audience_type === "BATCHES" && (
        <fieldset className="checklist">
          <legend>Batches of {branchName ?? "this branch"}</legend>
          {(batches.data?.items ?? []).filter((b) => b.status === "ACTIVE").map((b) => (
            <label key={b.batch_id} className="row">
              <input type="checkbox" checked={audience.batch_ids.includes(b.batch_id)} onChange={() => onChange({ ...audience,
                batch_ids: audience.batch_ids.includes(b.batch_id) ? audience.batch_ids.filter((x) => x !== b.batch_id) : [...audience.batch_ids, b.batch_id] })} />
              {b.name}
            </label>
          ))}
          {(batches.data?.items ?? []).filter((b) => b.status === "ACTIVE").length === 0 && !batches.loading && <EmptyState title="This branch has no active batches" />}
        </fieldset>
      )}
      {audience.audience_type === "STUDENTS" && <StudentPicker providerId={providerId} chosen={audience.student_ids} onChange={(ids) => onChange({ ...audience, student_ids: ids })} />}
      <div className="audience-summary" aria-live="polite">
        {preview.loading ? <Spinner label="Checking eligible students" /> : preview.error ? <Alert kind="error">{preview.error}</Alert> : preview.data && (
          <>
            <strong>{preview.data.eligible_count}</strong> student(s) are eligible right now. The final list is resolved again when the exam is generated, from current enrollments; nobody is assigned twice.
            {preview.data.not_eligible_student_ids.length > 0 && <Alert kind="warn">Not actively enrolled with this provider: {preview.data.not_eligible_student_ids.join(", ")}</Alert>}
          </>
        )}
      </div>
    </Card>
  );
}

function StudentPicker({ providerId, chosen, onChange }: { providerId: string; chosen: string[]; onChange: (ids: string[]) => void }) {
  const [q, setQ] = useState("");
  const [typed, setTyped] = useState("");
  const params = new URLSearchParams({ provider_id: providerId, status: "ACTIVE", page_size: "20" });
  if (q.trim().length >= 3) params.set("q", q.trim());
  const list = useApi<Page<DirStudent>>(`/api/v1/students?${params.toString()}`);
  const toggle = (id: string) => onChange(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);
  return (
    <div className="stack">
      <div className="toolbar">
        <SearchField label="Find students (Student ID, email or phone)" value={q} onChange={setQ} />
        <Field label="Or add Student IDs (comma-separated)" value={typed} onChange={(e) => setTyped(e.target.value)} />
        <Button variant="secondary" disabled={!typed.trim()} onClick={() => {
          const ids = typed.split(/[\s,;]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);
          onChange([...new Set([...chosen, ...ids])]);
          setTyped("");
        }}>Add</Button>
      </div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      <DataTable rows={list.data?.items ?? []} rowKey={(s) => s.student_id} empty={<EmptyState title="No matching students" />}
        columns={[
          { header: "Select", render: (s) => <input type="checkbox" aria-label={`Select ${s.student_id}`} checked={chosen.includes(s.student_id)} onChange={() => toggle(s.student_id)} /> },
          { header: "Student ID", render: (s) => s.student_id }, { header: "Name", render: (s) => s.name ?? "—" },
        ]} />
      {chosen.length > 0 && (
        <p className="small">Chosen ({chosen.length}): {chosen.map((id) => (
          <button key={id} className="chip" onClick={() => toggle(id)} aria-label={`Remove ${id}`}>{id} ×</button>
        ))}</p>
      )}
    </div>
  );
}

/** Papers & test creation: for each subject choose an existing paper version (this branch's or a platform paper), or upload one. */
function PaperSlots({ providerId, branchId, subjects, slots, onChange }: { providerId: string; branchId: string; subjects: Subject[];
  slots: Record<string, Slot>; onChange: (s: Record<string, Slot>) => void }) {
  const own = useApi<Page<PaperRow>>(`/api/v1/papers?scope=PROVIDER&branch_id=${branchId}&page_size=100`);
  const platform = useApi<Page<PaperRow>>("/api/v1/papers?scope=GLOBAL&page_size=100");
  const [uploading, setUploading] = useState<string | null>(null);
  const papers = [...(own.data?.items ?? []), ...(platform.data?.items ?? [])].filter((p) => p.latest_version && p.latest_version.status !== "WITHDRAWN");
  const choose = (subjectId: string, versionId: string) => {
    const next = { ...slots };
    const p = papers.find((x) => x.latest_version?.paper_version_id === versionId);
    if (!versionId || !p?.latest_version) delete next[subjectId];
    else next[subjectId] = { subject_id: subjectId, paper_version_id: versionId, label: `${p.title} v${p.latest_version.version_no}${p.scope === "GLOBAL" ? " (platform)" : ""}`,
      question_count: p.latest_version.question_count, total_marks: Number(p.latest_version.total_marks), status: p.latest_version.status };
    onChange(next);
  };
  if ((own.loading && !own.data) || (platform.loading && !platform.data)) return <Spinner />;
  return (
    <div className="stack">
      <p className="muted small">The exact version you choose is used; a later version never changes this test. Subjects left empty are not included.</p>
      {subjects.length === 0 && <EmptyState title="This provider offers no subjects yet" />}
      {subjects.map((s) => (
        <div key={s.subject_id} className="slot-row">
          <SelectField label={s.name} value={slots[s.subject_id]?.paper_version_id ?? ""} onChange={(e) => choose(s.subject_id, e.target.value)}>
            <option value="">Not included</option>
            {papers.filter((p) => p.subject_id === s.subject_id).map((p) => (
              <option key={p.paper_id} value={p.latest_version!.paper_version_id}>
                {p.title} v{p.latest_version!.version_no} · {p.latest_version!.question_count} q · {p.latest_version!.status.toLowerCase()}{p.scope === "GLOBAL" ? " · platform" : ""}
              </option>
            ))}
          </SelectField>
          <Button small variant="secondary" onClick={() => setUploading(s.subject_id)}>Upload a file…</Button>
        </div>
      ))}
      {uploading && (
        <InlineUpload branchId={branchId} providerId={providerId} subject={subjects.find((x) => x.subject_id === uploading)!}
          onClose={() => setUploading(null)} onDone={(slot) => { onChange({ ...slots, [slot.subject_id]: slot }); setUploading(null); own.reload(); }} />
      )}
    </div>
  );
}

function InlineUpload({ branchId, subject, onClose, onDone }: { branchId: string; providerId: string; subject: Subject; onClose: () => void; onDone: (s: Slot) => void }) {
  const [title, setTitle] = useState("");
  const [errors, setErrors] = useState<{ row: number | null; field: string | null; message: string }[]>([]);
  const file = useRef<HTMLInputElement>(null);
  const key = useRef<HTMLInputElement>(null);
  const { busy, error, run } = useAction();
  return (
    <Card>
      <h3>Upload a {subject.name} paper</h3>
      <p className="muted small">Validated completely: one problem rejects the whole file. Add a separate answer key (.csv/.txt, “number,answer”) if the answers are not in the file.</p>
      {error && <Alert kind="error">{error}</Alert>}
      {errors.length > 0 && <DataTable rows={errors} rowKey={(e) => `${e.row}-${e.field}-${e.message}`}
        columns={[{ header: "Row / question", render: (e) => e.row ?? "—" }, { header: "Field", render: (e) => e.field ?? "—" }, { header: "Problem", render: (e) => e.message }]} />}
      <Field label="Paper title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <div className="field"><label htmlFor="wiz-file">Question file</label><input id="wiz-file" type="file" ref={file} accept=".csv,.xlsx,.docx,.pdf,.png,.jpg,.jpeg" /></div>
      <div className="field"><label htmlFor="wiz-key">Answer key (optional)</label><input id="wiz-key" type="file" ref={key} accept=".csv,.txt" /></div>
      <div className="row">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={title.trim().length < 3} onClick={() => void run(async () => {
          setErrors([]);
          const f = file.current?.files?.[0];
          if (!f) throw new Error("Choose the question file.");
          const form = new FormData();
          form.append("file", f);
          form.append("title", title.trim());
          const k = key.current?.files?.[0];
          if (k) form.append("answer_key", k);
          try {
            const r = await api<{ paper_version_id: string; version_no: number; question_count: number; total_marks: string; status: string }>(
              `/api/v1/branches/${branchId}/subjects/${subject.subject_id}/papers/upload`, { method: "POST", form });
            onDone({ subject_id: subject.subject_id, paper_version_id: r.paper_version_id, label: `${title.trim()} v${r.version_no} (uploaded)`,
              question_count: r.question_count, total_marks: Number(r.total_marks), status: r.status });
          } catch (e) {
            if (e instanceof ApiError && e.code === "EXTRACTION_FAILED") { setErrors((e.extra.errors as typeof errors) ?? []); throw new Error("The paper was rejected; nothing was imported.", { cause: e }); }
            throw e;
          }
        })}>Validate & use</Button>
      </div>
    </Card>
  );
}
