import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatCard, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

type Subject = { subject_id: string; name: string };
type Batch = { batch_id: string; name: string; status: string };
type PaperRow = { paper_id: string; subject_id: string; title: string; latest_version: { status: string; version_no: number } | null };
type Plan = { plan_id: string; name: string; duration_minutes: number; start_at: string; end_at: string; generate_at: string | null; auto_publish: boolean; status: string; batch_ids: string[]; slots: { subject_id: string; paper_id: string | null; paper_version_id: string | null }[] };
type Job = { job_id: string; status: string; exam_id: string | null; error: string | null; attempts: number; run_key: string };
type Exam = { exam_id: string; name: string; status: string; start_at: string | null; end_at: string | null; duration_minutes: number | null; total_questions: number | null; total_marks: string | null;
  missing_subjects: { subject: string | null; reason: string; detail: string }[]; papers: { subject_id: string; source_provider_id: string; paper_version_id: string; question_count: number }[]; assigned_students: number; delivery_status: string | null };

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");
const toLocalInput = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

export function ExamPlansPage() {
  return <NeedBranch>{({ providerId, branchId }) => <Plans providerId={providerId} branchId={branchId} />}</NeedBranch>;
}

function Plans({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const list = useApi<Page<Plan>>(`/api/v1/branches/${branchId}/exam-plans?page=${page}&page_size=20`);
  const [creating, setCreating] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const gen = useAction();
  return (
    <>
      <PageHeader title="Exam plans" subtitle="Which papers make an exam, who sits it and when it is generated" actions={can("examplan.manage") && <Button onClick={() => setCreating(true)}>New plan</Button>} />
      <div className="stack">
        {gen.error && <Alert kind="error">{gen.error}</Alert>}
        {job && <Alert kind={job.status === "FAILED" ? "error" : job.status === "PARTIAL" ? "warn" : "success"}>
          Generation {job.status.toLowerCase()}{job.error ? `: ${job.error}` : ""}. {job.exam_id && <Link to={`/exams/${job.exam_id}`}>Open the exam</Link>}
        </Alert>}
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(p) => p.plan_id} empty={<EmptyState title="No exam plans" hint="Create a plan to generate exams automatically." />}
              columns={[
                { header: "Plan", render: (p) => p.name }, { header: "Window", render: (p) => `${fmt(p.start_at)} → ${fmt(p.end_at)}` },
                { header: "Duration", render: (p) => `${p.duration_minutes} min` }, { header: "Subjects", render: (p) => p.slots.length },
                { header: "Auto-generate", render: (p) => p.generate_at ? fmt(p.generate_at) : "Manual" }, { header: "Publish", render: (p) => (p.auto_publish ? "Automatic" : "Manual") },
                { header: "", render: (p) => can("exam.generate") && p.status === "ACTIVE" && <Button small onClick={() => void gen.run(async () => { setJob(await api<Job>(`/api/v1/exam-plans/${p.plan_id}/generate`, { method: "POST", body: {} })); })} busy={gen.busy}>Generate now</Button> },
              ]} />
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </div>
      {creating && <NewPlan providerId={providerId} branchId={branchId} onClose={() => setCreating(false)} onDone={() => { setCreating(false); list.reload(); }} />}
    </>
  );
}

function NewPlan({ providerId, branchId, onClose, onDone }: { providerId: string; branchId: string; onClose: () => void; onDone: () => void }) {
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const batches = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const papers = useApi<Page<PaperRow>>(`/api/v1/branches/${branchId}/papers?page_size=100`);
  const [f, setF] = useState(() => {
    const soon = new Date(Date.now() + 2 * 86400000); // default: two days from now, evaluated once
    return { name: "", duration: 180, start: toLocalInput(soon), end: toLocalInput(new Date(soon.getTime() + 6 * 3600000)), auto: true, generateAt: "" };
  });
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [slots, setSlots] = useState<Record<string, string>>({});
  const { busy, error, run } = useAction();
  const toggle = (id: string) => setBatchIds((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  const slotList = Object.entries(slots).filter(([, p]) => p).map(([subject_id, paper_id]) => ({ subject_id, paper_id }));
  return (
    <Modal title="New exam plan" onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Plan name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <div className="grid cols-2">
        <Field label="Starts (students may begin from)" type="datetime-local" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} />
        <Field label="Ends (last time to begin)" type="datetime-local" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} />
        <Field label="Duration (minutes, one shared timer)" type="number" min={1} max={720} value={f.duration} onChange={(e) => setF({ ...f, duration: Number(e.target.value) })} />
        <Field label="Auto-generate at (optional)" type="datetime-local" value={f.generateAt} onChange={(e) => setF({ ...f, generateAt: e.target.value })} />
      </div>
      <label className="row"><input type="checkbox" checked={f.auto} onChange={(e) => setF({ ...f, auto: e.target.checked })} />Publish to students automatically after generation</label>
      <h3 style={{ marginTop: "var(--space-4)" }}>Batches</h3>
      {(batches.data?.items ?? []).filter((b) => b.status === "ACTIVE").map((b) => <label key={b.batch_id} className="row"><input type="checkbox" checked={batchIds.includes(b.batch_id)} onChange={() => toggle(b.batch_id)} />{b.name}</label>)}
      <h3 style={{ marginTop: "var(--space-4)" }}>Papers per subject</h3>
      <p className="muted small">The latest published version is used at generation time. Missing subjects are reported and the exam is generated from the rest.</p>
      {(subjects.data ?? []).map((s) => (
        <SelectField key={s.subject_id} label={s.name} value={slots[s.subject_id] ?? ""} onChange={(e) => setSlots({ ...slots, [s.subject_id]: e.target.value })}>
          <option value="">Not included</option>
          {(papers.data?.items ?? []).filter((p) => p.subject_id === s.subject_id).map((p) => <option key={p.paper_id} value={p.paper_id}>{p.title} ({p.latest_version?.status ?? "no version"})</option>)}
        </SelectField>
      ))}
      <Button busy={busy} disabled={f.name.length < 3 || batchIds.length === 0 || slotList.length === 0} onClick={() => void run(async () => {
        await api(`/api/v1/branches/${branchId}/exam-plans`, { method: "POST", body: { name: f.name, duration_minutes: f.duration, start_at: new Date(f.start).toISOString(), end_at: new Date(f.end).toISOString(), generate_at: f.generateAt ? new Date(f.generateAt).toISOString() : null, auto_publish: f.auto, batch_ids: batchIds, slots: slotList } });
        onDone();
      })}>Create plan</Button>
    </Modal>
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
      <PageHeader title="Exams & results" subtitle="Generated exams and their delivery to students" />
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

type Results = { window_closed: boolean; total_marks: string; summary: { assigned: number; submitted: number; not_submitted: number; average: number | null; highest: number | null; lowest: number | null };
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
  return (
    <>
      <PageHeader crumbs={["Exams", e.name]} title={e.name} subtitle={`${fmt(e.start_at)} → ${fmt(e.end_at)} · ${e.duration_minutes} min shared timer`}
        actions={<><StatusBadge status={e.status} />
          {e.status === "SCHEDULED" && can("exam.publish") && <Button busy={act.busy} onClick={() => void act.run(async () => { await api(`/api/v1/exams/${examId}/publish`, { method: "POST" }); exam.reload(); })}>Publish to students</Button>}
          {e.status !== "CANCELLED" && can("exam.schedule") && <Button variant="danger" onClick={() => setCancelling(true)}>Cancel exam</Button>}</>} />
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <div className="stack">
        <div className="grid stats">
          <StatCard label="Questions" value={e.total_questions ?? 0} /><StatCard label="Total marks" value={e.total_marks ?? 0} /><StatCard label="Assigned students" value={e.assigned_students} />
          <StatCard label="Delivery to students" value={e.delivery_status ?? "—"} tone={e.delivery_status === "FAILED" ? "danger" : undefined} />
        </div>
        {e.missing_subjects.length > 0 && (
          <Alert kind="warn"><strong>Generated without {e.missing_subjects.length} subject(s):</strong>
            <ul>{e.missing_subjects.map((m) => <li key={m.reason + m.subject}>{m.subject ?? "Subject"} — {m.detail} <span className="muted">({m.reason})</span></li>)}</ul>
            Responsible teachers and admins were notified. A paper approved later applies to the next exam, not this one.</Alert>
        )}
        <Card><h2>Paper manifest (immutable)</h2>
          <DataTable rows={e.papers} rowKey={(p) => p.paper_version_id} columns={[{ header: "Paper version", render: (p) => <Link to={`/papers/${p.paper_version_id}`}>{p.paper_version_id.slice(0, 8)}</Link> }, { header: "Questions", render: (p) => p.question_count }, { header: "Source provider", render: (p) => p.source_provider_id.slice(0, 8) }]} />
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
                  columns={[{ header: "Rank", render: (r) => r.rank }, { header: "Student", render: (r) => `${r.name ?? ""} ${r.student_id}` }, { header: "Marks", render: (r) => `${r.obtained_marks}/${r.total_marks}` }, { header: "Accuracy", render: (r) => `${r.accuracy.toFixed(1)}%` }]} />
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
