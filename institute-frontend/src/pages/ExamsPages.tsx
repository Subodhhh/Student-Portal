import { useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, PageHeader, Pagination, SelectField, Spinner, StatCard, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

type Plan = { plan_id: string; name: string; duration_minutes: number; start_at: string; end_at: string; generate_at: string | null; auto_publish: boolean; status: string;
  audience_type: string; batch_ids: string[]; student_ids: string[]; marking_mode: string; marks_correct: number | null; marks_wrong: number | null;
  global_test_id: string | null; global_test_name: string | null; slots: { subject_id: string; paper_id: string | null; paper_version_id: string | null }[] };
type Job = { job_id: string; status: string; exam_id: string | null; error: string | null; attempts: number; run_key: string };
type Exam = { exam_id: string; name: string; status: string; start_at: string | null; end_at: string | null; duration_minutes: number | null; total_questions: number | null; total_marks: string | null;
  missing_subjects: { subject: string | null; reason: string; detail: string }[]; papers: { subject_id: string; source_provider_id: string | null; platform_paper?: boolean; paper_version_id: string; question_count: number }[];
  assigned_students: number; assignment_status?: Record<string, number>; delivery_status: string | null;
  unified_global?: boolean; visible_subject_ids?: string[] | null;   // a provider's view of a unified Global Test exam: its own subjects only
  settings?: { marking?: { mode: string; correct?: number; wrong?: number }; audience_type?: string; global_test_id?: string | null; skipped_duplicates?: number } | null };

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");

const AUDIENCE: Record<string, string> = { BATCHES: "Batches", BRANCH: "Whole branch", INSTITUTE: "Whole institute", STUDENTS: "Selected students" };

export function ExamPlansPage() {
  const [params] = useSearchParams();
  // Global Tests moved to Test -> Global Tests; old links (?tab=global) go there.
  if (params.get("tab") === "global") return <Navigate to="/tests/global" replace />;
  return <NeedBranch>{({ branchId }) => <Plans branchId={branchId} />}</NeedBranch>;
}

function Plans({ branchId }: { branchId: string }) {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const list = useApi<Page<Plan>>(`/api/v1/branches/${branchId}/exam-plans?page=${page}&page_size=20`);
  const [job, setJob] = useState<Job | null>(null);
  const gen = useAction();
  return (
    <>
      <PageHeader subtitle="Plans generate exams automatically from approved papers and the current audience. Exams already generated never change."
        actions={can("examplan.manage") && <Link className="btn" to="/exam-plans/new">Create a test</Link>} />
      <div className="stack">
        {gen.error && <Alert kind="error">{gen.error}</Alert>}
        {job && <Alert kind={job.status === "FAILED" ? "error" : job.status === "PARTIAL" ? "warn" : "success"}>
          Generation {job.status.toLowerCase()}{job.error ? `: ${job.error}` : ""}. {job.exam_id && <Link to={`/exams/${job.exam_id}`}>Open the exam</Link>}
        </Alert>}
        {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
        {list.loading && !list.data ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(p) => p.plan_id} empty={<EmptyState title="No exam plans" hint="Create a test to generate exams automatically." />}
              columns={[
                { header: "Plan", render: (p) => <><strong>{p.name}</strong>{p.global_test_name && <div className="muted small">Global Test: {p.global_test_name}</div>}</> },
                { header: "Audience", render: (p) => `${AUDIENCE[p.audience_type] ?? p.audience_type}${p.audience_type === "BATCHES" ? ` (${p.batch_ids.length})` : p.audience_type === "STUDENTS" ? ` (${p.student_ids.length})` : ""}` },
                { header: "Window", render: (p) => `${fmt(p.start_at)} → ${fmt(p.end_at)}` },
                { header: "Duration", render: (p) => `${p.duration_minutes} min` },
                { header: "Marking", render: (p) => (p.marking_mode === "UNIFORM" ? `+${p.marks_correct} / −${p.marks_wrong}` : "Per question") },
                { header: "Subjects", render: (p) => p.slots.length },
                { header: "Generates", render: (p) => (p.generate_at ? fmt(p.generate_at) : "Manually") },
                { header: "Status", render: (p) => <StatusBadge status={p.status} /> },
                { header: "Actions", render: (p) => (
                  <div className="row">
                    {can("exam.generate") && p.status === "ACTIVE" && <Button small busy={gen.busy} onClick={() => void gen.run(async () => {
                      setJob(await api<Job>(`/api/v1/exam-plans/${p.plan_id}/generate`, { method: "POST", body: {} }));
                    })}>Generate now</Button>}
                    {can("examplan.manage") && p.status === "DRAFT" && <Button small variant="secondary" busy={gen.busy} onClick={() => void gen.run(async () => {
                      await api(`/api/v1/exam-plans/${p.plan_id}`, { method: "PATCH", body: { status: "ACTIVE" } });
                      list.reload();
                    })}>Activate</Button>}
                  </div>
                ) },
              ]} />
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </div>
    </>
  );
}

export function ExamsPage() {
  return <NeedBranch>{({ branchId }) => <ExamList branchId={branchId} />}</NeedBranch>;
}

function ExamList({ branchId }: { branchId: string }) {
  const [page, setPage] = useState(1);
  const list = useApi<Page<Exam>>(`/api/v1/branches/${branchId}/exams?page=${page}&page_size=20`);
  return (
    <>
      <PageHeader subtitle="Generated exams and their delivery to students" />
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading ? <Spinner /> : (
        <div className="stack">
          <DataTable rows={list.data?.items ?? []} rowKey={(e) => e.exam_id} empty={<EmptyState title="No exams generated yet" />}
            columns={[
              { header: "Exam", render: (e) => <Link to={`/exams/${e.exam_id}`}>{e.name}</Link> }, { header: "Window", render: (e) => `${fmt(e.start_at)} → ${fmt(e.end_at)}` },
              { header: "Questions", render: (e) => e.total_questions ?? "—" }, { header: "Students", render: (e) => e.assigned_students },
              { header: "Status", render: (e) => <StatusBadge status={e.status} /> }, { header: "Delivery", render: (e) => e.delivery_status ? <StatusBadge status={e.delivery_status} /> : "—" },
              { header: "Gaps", render: (e) => (e.missing_subjects.length ? <StatusBadge status="PARTIAL" /> : "") },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </div>
      )}
    </>
  );
}

type Results = { window_closed: boolean; total_marks: string; subject_view?: boolean; visible_subjects?: string[] | null; summary: { assigned: number; submitted: number; not_submitted: number; average: number | null; highest: number | null; lowest: number | null };
  results: { rank: number; student_id: string; name: string | null; obtained_marks: number; total_marks: number; accuracy: number }[] };

type Correction = { correction_id: string; kind: "MARKS_ADJUSTMENT" | "ANSWER_KEY"; status: string; student_id: string | null; reason: string; actor: string; created_at: string;
  previous_marks: number | null; new_marks: number | null; error: string | null; key_changes: { question_order: number; previous: string; new: string }[] | null;
  reevaluation: { changed: string[]; unchanged: number; skipped_manual: string[] } | null };

/** The Idempotency-Key is created once per form and kept until the correction succeeds or the inputs change,
 * so retrying after a "saved, retry to complete" error finishes the same correction instead of making a second one. */
function useCorrectionKey(signature: string) {
  const [state, setState] = useState({ signature, key: crypto.randomUUID() });
  if (state.signature !== signature) setState({ signature, key: crypto.randomUUID() });
  return { key: state.key, renew: () => setState({ signature, key: crypto.randomUUID() }) };
}

function CorrectionsCard({ examId, canCorrect, onChanged }: { examId: string; canCorrect: boolean; onChanged: () => void }) {
  const list = useApi<Correction[]>(`/api/v1/exams/${examId}/result-corrections`);
  const act = useAction();
  const [student, setStudent] = useState("");
  const [marks, setMarks] = useState("");
  const [reason, setReason] = useState("");
  const [order, setOrder] = useState("");
  const [option, setOption] = useState("A");
  const [keyReason, setKeyReason] = useState("");
  const adjustKey = useCorrectionKey(`${student}|${marks}|${reason}`);
  const keyKey = useCorrectionKey(`${order}|${option}|${keyReason}`);
  const done = () => { list.reload(); onChanged(); };
  return (
    <Card>
      <h2>Result corrections</h2>
      <p className="muted small">Corrections are recorded permanently with the reason and who made them; the original result is never overwritten. Affected students are told their result was updated.</p>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {canCorrect && (
        <div className="grid cols-2">
          <div className="stack"><h3>Correct one student's marks</h3>
            <Field label="Student ID" value={student} onChange={(e) => setStudent(e.target.value.trim().toUpperCase())} placeholder="STU000001" />
            <Field label="Correct marks" type="number" value={marks} onChange={(e) => setMarks(e.target.value)} />
            <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button busy={act.busy} disabled={!student || marks === "" || reason.trim().length < 5}
              onClick={() => void act.run(async () => { await api(`/api/v1/exams/${examId}/results/${student}/adjustments`, { method: "POST", body: { obtained_marks: Number(marks), reason }, headers: { "Idempotency-Key": adjustKey.key } }); adjustKey.renew(); setStudent(""); setMarks(""); setReason(""); done(); })}>Apply correction</Button></div>
          <div className="stack"><h3>Correct an answer key and re-evaluate</h3>
            <Field label="Question number" type="number" min={1} value={order} onChange={(e) => setOrder(e.target.value)} />
            <SelectField label="Correct option" value={option} onChange={(e) => setOption(e.target.value)}>{["A", "B", "C", "D"].map((o) => <option key={o}>{o}</option>)}</SelectField>
            <TextAreaField label="Reason (required)" value={keyReason} onChange={(e) => setKeyReason(e.target.value)} />
            <p className="muted small">Results a person adjusted by hand are not re-scored; they are listed below as skipped.</p>
            <Button variant="danger" busy={act.busy} disabled={!order || keyReason.trim().length < 5}
              onClick={() => void act.run(async () => { await api(`/api/v1/exams/${examId}/answer-key-corrections`, { method: "POST", body: { changes: [{ question_order: Number(order), correct_option: option }], reason: keyReason }, headers: { "Idempotency-Key": keyKey.key } }); keyKey.renew(); setOrder(""); setKeyReason(""); done(); })}>Correct key and re-evaluate</Button></div>
        </div>
      )}
      <h3>History</h3>
      <DataTable rows={list.data ?? []} rowKey={(c) => c.correction_id} empty={<EmptyState title="No corrections yet" />}
        columns={[{ header: "When", render: (c) => fmt(c.created_at) }, { header: "By", render: (c) => c.actor },
          { header: "What", render: (c) => c.kind === "MARKS_ADJUSTMENT" ? `${c.student_id}: ${c.previous_marks ?? "?"} → ${c.new_marks ?? "?"}`
            : `Key: ${(c.key_changes ?? []).map((k) => `Q${k.question_order} ${k.previous}→${k.new}`).join(", ") || "pending"}; ${c.reevaluation ? `${c.reevaluation.changed.length} result(s) changed${c.reevaluation.skipped_manual.length ? `, ${c.reevaluation.skipped_manual.length} skipped (manual)` : ""}` : "not re-evaluated yet"}` },
          { header: "Reason", render: (c) => c.reason }, { header: "Status", render: (c) => <StatusBadge status={c.status} /> }]} />
      {(list.data ?? []).some((c) => c.status !== "APPLIED") && <p className="muted small">A correction marked FAILED was saved but not completed. Submit the same correction again to finish it.</p>}
    </Card>
  );
}

export function ExamDetailPage() {
  const { examId = "" } = useParams();
  const { can } = useAuth();
  const exam = useApi<Exam>(`/api/v1/exams/${examId}`);
  const [showResults, setShowResults] = useState(false);
  const results = useApi<Results>(showResults ? `/api/v1/exams/${examId}/results` : null);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  const act = useAction();
  if (exam.loading) return <Spinner />;
  if (exam.error || !exam.data) return <Alert kind="error">{exam.error || "Not found"}</Alert>;
  const e = exam.data;
  const partial = !!e.visible_subject_ids;      // unified Global Test, provider view: own subjects and students only, no control actions
  return (
    <>
      <PageHeader crumbs={[<Link key="exams" to="/exams">Exams & Results</Link>, e.name]} title={e.name} subtitle={`${fmt(e.start_at)} → ${fmt(e.end_at)} · ${e.duration_minutes} min shared timer`}
        actions={<><StatusBadge status={e.status} />
          {!partial && e.status === "SCHEDULED" && can("exam.publish") && <Button busy={act.busy} onClick={() => void act.run(async () => { await api(`/api/v1/exams/${examId}/publish`, { method: "POST" }); exam.reload(); })}>Publish to students</Button>}
          {!partial && e.status !== "CANCELLED" && can("exam.schedule") && <Button variant="danger" onClick={() => setCancelling(true)}>Cancel exam</Button>}</>} />
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <div className="stack">
        {partial && <Alert kind="info">One Global Test exam per student, assembled from each student's own institutes. You see only the subjects your
          institute supplied and your own students; the overall score is visible only to the student and the platform.</Alert>}
        <div className="grid stats">
          <StatCard label="Questions" value={e.total_questions ?? 0} /><StatCard label="Total marks" value={e.total_marks ?? 0} /><StatCard label="Assigned students" value={e.assigned_students} />
          <StatCard label="Delivery to students" value={e.delivery_status ?? "—"} tone={e.delivery_status === "FAILED" ? "danger" : undefined} />
        </div>
        {e.assignment_status && Object.keys(e.assignment_status).length > 0 && (
          <p className="small">Attempts: {Object.entries(e.assignment_status).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ")}</p>
        )}
        <Card>
          <h2>Rules this exam was generated with (immutable)</h2>
          <dl className="facts">
            <dt>Marking</dt><dd>{e.settings?.marking?.mode === "UNIFORM" ? `+${e.settings.marking.correct} correct, −${e.settings.marking.wrong} wrong, 0 unanswered` : "Each question's own marks, 0 unanswered"}</dd>
            <dt>Timer</dt><dd>One shared timer of {e.duration_minutes} minutes; starting inside the window gives the full duration</dd>
            <dt>Attempts</dt><dd>One attempt per student, resumable; students who never start are marked missed</dd>
            <dt>Results</dt><dd>After evaluation; answers reviewable once the window has closed</dd>
            {e.settings?.global_test_id && <><dt>Source</dt><dd>{e.unified_global ? "Global Test date: each subject is the paper its institute submitted for the student" : "Global Test (platform papers, unchanged)"}</dd></>}
            {!!e.settings?.skipped_duplicates && <><dt>Duplicates</dt><dd>{e.settings.skipped_duplicates} student(s) not assigned: they already have this test for the same time</dd></>}
          </dl>
        </Card>
        {e.missing_subjects.length > 0 && (
          <Alert kind="warn"><strong>Generated without {e.missing_subjects.length} subject(s):</strong>
            <ul>{e.missing_subjects.map((m) => <li key={m.reason + m.subject}>{m.subject ?? "Subject"} — {m.detail} <span className="muted">({m.reason})</span></li>)}</ul>
            Responsible teachers and admins were notified. A paper approved later applies to the next exam, not this one.</Alert>
        )}
        <Card><h2>Paper manifest (immutable)</h2>
          <DataTable rows={e.papers} rowKey={(p) => p.paper_version_id} columns={[{ header: "Paper version", render: (p) => <Link to={`/papers/${p.paper_version_id}`}>{p.paper_version_id.slice(0, 8)}</Link> }, { header: "Questions", render: (p) => p.question_count }, { header: "Source", render: (p) => (p.platform_paper ? "Platform paper" : p.source_provider_id ? `Provider ${p.source_provider_id.slice(0, 8)}` : "—") }]} />
        </Card>
        {can("result.read") && e.status === "PUBLISHED" && (
          <Card>
            <div className="row between"><h2>Results</h2>{!showResults && <Button variant="secondary" onClick={() => setShowResults(true)}>Load results</Button>}</div>
            {results.loading && <Spinner />}{results.error && <Alert kind="error">{results.error}</Alert>}
            {results.data && (
              <>
                <div className="grid stats"><StatCard label="Submitted" value={`${results.data.summary.submitted}/${results.data.summary.assigned}`} /><StatCard label="Average" value={results.data.summary.average ?? "—"} /><StatCard label="Highest" value={results.data.summary.highest ?? "—"} /><StatCard label="Lowest" value={results.data.summary.lowest ?? "—"} /></div>
                {!results.data.window_closed && <p className="muted small">The exam window is still open; results will keep arriving.</p>}
                <DataTable rows={results.data.results} rowKey={(r) => r.student_id} empty={<EmptyState title="No submissions yet" />}
                  columns={[{ header: "Rank", render: (r) => r.rank }, { header: "Student", render: (r) => `${r.name ?? ""} ${r.student_id}` }, { header: results.data.subject_view ? `Marks (${(results.data.visible_subjects ?? []).join(", ") || "your subjects"})` : "Marks", render: (r) => `${r.obtained_marks}/${r.total_marks}` }, { header: "Accuracy", render: (r) => `${r.accuracy.toFixed(1)}%` }]} />
              </>
            )}
          </Card>
        )}
        {e.status === "PUBLISHED" && can("result.correct") && <CorrectionsCard examId={examId} canCorrect={can("result.correct")} onChanged={() => { if (showResults) results.reload(); }} />}
      </div>
      {cancelling && (
        <ConfirmDialog title="Cancel exam" message="Students will no longer be able to start it. Attempts already submitted are kept." danger busy={act.busy} onCancel={() => setCancelling(false)}
          onConfirm={() => void act.run(async () => { await api(`/api/v1/exams/${examId}/cancel`, { method: "POST", body: { reason } }); setCancelling(false); exam.reload(); })}>
          <TextAreaField label="Reason (required)" value={reason} onChange={(e2) => setReason(e2.target.value)} />
        </ConfirmDialog>
      )}
    </>
  );
}
