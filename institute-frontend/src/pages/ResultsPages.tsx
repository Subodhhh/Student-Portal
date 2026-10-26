import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, DataTable, EmptyState, Field, PageHeader, Pagination, SelectField, Spinner, StatCard, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { IntegrityCard } from "./ExamIntegrity";
import { StudentCount } from "../components/StudentCount";

// Results: submitted attempts and evaluated performance, per exam. Exams and their lifecycle are on the Exams page. Every number comes from
// the existing results API, which scopes rows to the viewer (platform: all; provider/branch admins: their students; teachers: their
// teaching scope; a provider's view of a unified Global Test: only its own subjects, never the overall score).

type ExamRow = { exam_id: string; name: string; status: string; start_at: string | null; end_at: string | null; assigned_students: number;
  unified_global?: boolean; assignment_status?: Record<string, number> };

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");

export function ResultsPage() {
  return <NeedBranch>{({ branchId }) => <ResultExams branchId={branchId} />}</NeedBranch>;
}

function ResultExams({ branchId }: { branchId: string }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const list = useApi<Page<ExamRow>>(`/api/v1/branches/${branchId}/exams?status=PUBLISHED&page=${page}&page_size=20${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`);
  return (
    <>
      <PageHeader subtitle="Submitted attempts and evaluated performance: for each published exam, who submitted, their scores and subject-wise breakdown, and any result corrections. Exams themselves are managed on the Exams page." />
      <div className="toolbar"><Field label="Search exams" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Name" /></div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <div className="stack">
          <DataTable rows={list.data?.items ?? []} rowKey={(e) => e.exam_id} empty={<EmptyState title={q ? "No exam matches" : "No published exams yet"} />}
            columns={[
              { header: "Exam", render: (e) => <><Link to={`/results/${e.exam_id}`}>{e.name}</Link>{e.unified_global && <div className="muted small">Global Test date</div>}</> },
              { header: "Available", render: (e) => `${fmt(e.start_at)} → ${fmt(e.end_at)}` },
              { header: "Assigned", render: (e) => <StudentCount count={e.assigned_students} url={`/api/v1/exams/${e.exam_id}/assignments`} title={`Students assigned to ${e.name}`} context="The students this exam is assigned to that you may see, with their attempt status." /> },
              { header: "Completed", render: (e) => e.assignment_status?.COMPLETED ?? 0 },
              { header: "", render: (e) => <Link to={`/results/${e.exam_id}`}>View results</Link> },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </div>
      )}
    </>
  );
}

export function ResultDetailPage() {
  const { examId = "" } = useParams();
  const { can } = useAuth();
  const exam = useApi<ExamRow>(`/api/v1/exams/${examId}`);
  const results = useApi<Results>(exam.data?.status === "PUBLISHED" ? `/api/v1/exams/${examId}/results` : null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("rank");
  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    const found = (results.data?.results ?? []).filter((r) => !term || r.student_id.toLowerCase().includes(term) || (r.name ?? "").toLowerCase().includes(term));
    return sort === "name" ? [...found].sort((a, b) => (a.name ?? a.student_id).localeCompare(b.name ?? b.student_id)) : found;
  }, [results.data, q, sort]);
  if (exam.loading) return <Spinner />;
  if (exam.error || !exam.data) return <Alert kind="error">{exam.error || "Not found"}</Alert>;
  const e = exam.data;
  const r = results.data;
  return (
    <>
      <PageHeader crumbs={[<Link key="results" to="/results">Results</Link>, e.name]} title={e.name} subtitle={`${fmt(e.start_at)} → ${fmt(e.end_at)}`}
        actions={<><StatusBadge status={e.status} />{can("exam.read") && <Link className="btn secondary" to={`/exams/${examId}`}>Exam</Link>}</>} />
      <div className="stack">
        {e.status !== "PUBLISHED" && <Alert kind="info">Results exist only for published exams.</Alert>}
        {results.loading && <Spinner />}{results.error && <Alert kind="error">{results.error}</Alert>}
        {r && (
          <Card>
            <h2>Results</h2>
            {r.subject_view && <Alert kind="info">You see only your institute's subjects ({(r.visible_subjects ?? []).join(", ") || "none"}) and your own students; the overall score is visible only to the student and the platform.</Alert>}
            <div className="grid stats"><StatCard label="Submitted" value={`${r.summary.submitted}/${r.summary.assigned}`} /><StatCard label="Not submitted" value={r.summary.not_submitted} />
              <StatCard label="Average" value={r.summary.average ?? "—"} /><StatCard label="Highest" value={r.summary.highest ?? "—"} /><StatCard label="Lowest" value={r.summary.lowest ?? "—"} /></div>
            {!r.window_closed && <p className="muted small">The exam window is still open; results will keep arriving.</p>}
            <div className="toolbar">
              <Field label="Search students" value={q} onChange={(ev) => setQ(ev.target.value)} placeholder="Name or Student ID" />
              <SelectField label="Sort" value={sort} onChange={(ev) => setSort(ev.target.value)}><option value="rank">By rank</option><option value="name">By name</option></SelectField>
            </div>
            <DataTable rows={rows} rowKey={(x) => x.student_id} empty={<EmptyState title={q ? "No student matches" : "No submissions yet"} />}
              columns={[{ header: "Rank", render: (x) => x.rank }, { header: "Student", render: (x) => <>{x.name ?? "—"}<div className="muted small">{x.student_id}</div></> },
                { header: "Status", render: () => <StatusBadge status="SUBMITTED" /> },
                { header: r.subject_view ? `Marks (${(r.visible_subjects ?? []).join(", ") || "your subjects"})` : "Total score", render: (x) => `${x.obtained_marks}/${x.total_marks}` },
                { header: "By subject", render: (x) => (x.subjects?.length ? x.subjects.map((s) => `${s.subject} ${s.obtained_marks}/${s.total_marks}`).join(" · ") : "—") },
                { header: "Accuracy", render: (x) => `${x.accuracy.toFixed(1)}%` }]} />
          </Card>
        )}
        {e.status === "PUBLISHED" && can("result.read") && <IntegrityCard examId={examId} />}
        {e.status === "PUBLISHED" && can("result.correct") && <CorrectionsCard examId={examId} canCorrect={can("result.correct")} onChanged={() => results.reload()} />}
      </div>
    </>
  );
}

type SubjectScore = { subject: string; obtained_marks: number; total_marks: number };
type ResultRow = { rank: number; student_id: string; name: string | null; obtained_marks: number; total_marks: number; accuracy: number; submitted_at?: string;
  subjects?: SubjectScore[] };
type Results = { window_closed: boolean; total_marks: string; subject_view?: boolean; visible_subjects?: string[] | null; summary: { assigned: number; submitted: number; not_submitted: number; average: number | null; highest: number | null; lowest: number | null };
  results: ResultRow[] };

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

