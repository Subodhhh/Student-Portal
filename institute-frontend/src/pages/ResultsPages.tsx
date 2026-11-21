import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Card, DataTable, EmptyState, Field, PageHeader, Pagination, SelectField, Spinner, StatCard, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { IntegrityCard } from "./ExamIntegrity";
import { StudentCount } from "../components/StudentCount";
import { TypeBadge } from "../components/questionBank";
import { numberProblem, type QuestionType } from "../services/questions";

// Results: submitted attempts and evaluated performance, per exam. Exams and their lifecycle are on the Exams page. Every number comes from
// the existing results API, which scopes rows to the viewer (platform: all; provider/branch admins: their students; teachers: their
// teaching scope; a provider's view of a unified Global Test: only its own subjects, never the overall score).

type ExamRow = { exam_id: string; name: string; status: string; start_at: string | null; end_at: string | null; assigned_students: number;
  unified_global?: boolean; assignment_status?: Record<string, number>; provider_name?: string | null; branch_name?: string | null };

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");

// Every published exam the viewer may read, across all of their providers and branches: no provider/branch to choose first.
export function ResultsPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const list = useApi<Page<ExamRow>>(`/api/v1/exams?status=PUBLISHED&page=${page}&page_size=20${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`);
  return (
    <div className="stack">
      <PageHeader subtitle="Submitted attempts and evaluated performance: for each published exam, who submitted, their scores and subject-wise breakdown, and any result corrections. Exams themselves are managed on the Exams page." />
      <div className="toolbar"><Field label="Search exams" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Name" /></div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(e) => e.exam_id} empty={<EmptyState title={q ? "No exam matches" : "No published exams yet"} />}
            columns={[
              { header: "Exam", render: (e) => <><Link to={`/results/${e.exam_id}`}>{e.name}</Link>{e.unified_global && <div className="muted small">Global Test date</div>}</> },
              { header: "Provider / branch", render: (e) => (e.unified_global ? "Global Test" : [e.provider_name, e.branch_name].filter(Boolean).join(" · ") || "—") },
              { header: "Available", render: (e) => `${fmt(e.start_at)} → ${fmt(e.end_at)}` },
              { header: "Assigned", render: (e) => <StudentCount count={e.assigned_students} url={`/api/v1/exams/${e.exam_id}/assignments`} title={`Students assigned to ${e.name}`} context="The students this exam is assigned to that you may see, with their attempt status." /> },
              { header: "Completed", render: (e) => e.assignment_status?.COMPLETED ?? 0 },
              { header: "", render: (e) => <Link to={`/results/${e.exam_id}`}>View results</Link> },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
    </div>
  );
}

export function ResultDetailPage() {
  const { examId = "" } = useParams();
  const { can } = useAuth();
  const exam = useApi<ExamRow>(`/api/v1/exams/${examId}`);
  // Requested together with the exam, not after it; its answer is shown only for a published exam (an unpublished one answers 409).
  const results = useApi<Results>(`/api/v1/exams/${examId}/results`);
  const published = exam.data?.status === "PUBLISHED";
  // The Super Admin's correction history, also requested at page open (the server still authorizes it); shown only in the card.
  const corrections = useApi<Correction[]>(can("result.correct") ? `/api/v1/exams/${examId}/result-corrections` : null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("rank");
  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    const found = ((published && results.data?.results) || []).filter((r) => !term || r.student_id.toLowerCase().includes(term) || (r.name ?? "").toLowerCase().includes(term));
    return sort === "name" ? [...found].sort((a, b) => (a.name ?? a.student_id).localeCompare(b.name ?? b.student_id)) : found;
  }, [published, results.data, q, sort]);
  if (exam.loading) return <Spinner />;
  if (exam.error || !exam.data) return <Alert kind="error">{exam.error || "Not found"}</Alert>;
  const e = exam.data;
  const r = published ? results.data : null;
  return (
    <>
      <PageHeader crumbs={[<Link key="results" to="/results">Results</Link>, e.name]} title={e.name} subtitle={`${fmt(e.start_at)} → ${fmt(e.end_at)}`}
        actions={<><StatusBadge status={e.status} />{can("exam.read") && <Link className="btn secondary" to={`/exams/${examId}`}>Exam</Link>}</>} />
      <div className="stack">
        {e.status !== "PUBLISHED" && <Alert kind="info">Results exist only for published exams.</Alert>}
        {published && results.loading && <Spinner />}{published && results.error && <Alert kind="error">{results.error}</Alert>}
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
              columns={[{ header: "Rank", render: (x) => x.rank ?? "—" }, { header: "Student", render: (x) => <>{x.name ?? "—"}<div className="muted small">{x.student_id}</div></> },
                { header: "Status", render: () => <StatusBadge status="SUBMITTED" /> },
                { header: r.subject_view ? `Marks (${(r.visible_subjects ?? []).join(", ") || "your subjects"})` : "Total score", render: (x) => `${x.obtained_marks}/${x.total_marks}` },
                { header: "By subject", render: (x) => (x.subjects?.length ? x.subjects.map((s) => `${s.subject} ${s.obtained_marks}/${s.total_marks}`).join(" · ") : "—") },
                { header: "Accuracy", render: (x) => `${x.accuracy.toFixed(1)}%` }]} />
          </Card>
        )}
        {r && !r.subject_view && <QuestionAnalysisCard examId={examId} />}
        {e.status === "PUBLISHED" && can("result.read") && <IntegrityCard examId={examId} />}
        {e.status === "PUBLISHED" && (can("result.correct") || (r?.global_test && can("result.correct.own"))) && (
          <CorrectionsCard examId={examId} platform={can("result.correct")} globalTest={Boolean(r?.global_test)} rows={r?.results ?? []}
            history={corrections} onChanged={() => results.reload()} />
        )}
      </div>
    </>
  );
}

type SubjectScore = { subject: string; obtained_marks: number; total_marks: number; paper_id?: string | null; adjustment?: number };
type ResultRow = { rank: number | null; student_id: string; name: string | null; obtained_marks: number; total_marks: number; accuracy: number; submitted_at?: string;
  subjects?: SubjectScore[] };
type Results = { window_closed: boolean; total_marks: string; subject_view?: boolean; global_test?: boolean; visible_subjects?: string[] | null; summary: { assigned: number; submitted: number; not_submitted: number; average: number | null; highest: number | null; lowest: number | null };
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

/** platform: the Super Admin's full corrections. Otherwise (a Global Test contributor) only the answer key of the contributor's own
 * papers; the server refuses any other question. */
export function CorrectionsCard({ examId, platform, globalTest, rows, onChanged, history }: { examId: string; platform: boolean; globalTest: boolean; rows: ResultRow[];
  onChanged: () => void; history?: ReturnType<typeof useApi<Correction[]>> }) {
  // the page may already have requested the history (history); otherwise the card requests it itself
  const own = useApi<Correction[]>(platform && !history ? `/api/v1/exams/${examId}/result-corrections` : null);
  const list = history ?? own;
  const [subjectMarks, setSubjectMarks] = useState<Record<string, string>>({});
  const act = useAction();
  const [student, setStudent] = useState("");
  const [marks, setMarks] = useState("");
  const [reason, setReason] = useState("");
  const [order, setOrder] = useState("");
  const [option, setOption] = useState("A");
  const [keyType, setKeyType] = useState<QuestionType>("MCQ_SINGLE");
  const [letters, setLetters] = useState<string[]>([]);
  const [nat, setNat] = useState({ value: "", kind: "EXACT", amount: "", min: "", max: "" });
  const [keyReason, setKeyReason] = useState("");
  // a Global Test: the corrected marks are given per subject of that student's attempt
  const subjects = globalTest ? (rows.find((x) => x.student_id === student)?.subjects ?? []) : [];
  const perSubject = subjects.length > 0;
  const subjectTotal = subjects.reduce((sum, x) => sum + Number(subjectMarks[x.subject] ?? ""), 0);
  const adjustKey = useCorrectionKey(`${student}|${marks}|${reason}|${JSON.stringify(subjectMarks)}`);
  const keyKey = useCorrectionKey(`${order}|${keyType}|${option}|${letters.join()}|${JSON.stringify(nat)}|${keyReason}`);
  // the corrected key in the shape of the question's type; the Student test service checks it against the delivered question
  const natReady = !numberProblem(nat.value) && (nat.kind !== "ABS" || !numberProblem(nat.amount))
    && (nat.kind !== "RANGE" || (!numberProblem(nat.min) && !numberProblem(nat.max)));
  const change = keyType === "MCQ_SINGLE" ? { correct_option: option } : keyType === "MCQ_MULTI" ? { key: { correct: letters } }
    : { key: { value: nat.value.trim(), tolerance: nat.kind === "ABS" ? { kind: "ABS", amount: nat.amount.trim() }
      : nat.kind === "RANGE" ? { kind: "RANGE", min: nat.min.trim(), max: nat.max.trim() } : { kind: "EXACT" } } };
  const keyReady = keyType === "MCQ_SINGLE" || (keyType === "MCQ_MULTI" ? letters.length > 0 : natReady);
  const done = () => { list.reload(); onChanged(); };
  return (
    <Card>
      <h2>Result corrections</h2>
      <p className="muted small">Corrections are recorded permanently with the reason and who made them; the original result is never overwritten. Affected students are told their result was updated.</p>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {!platform && <p className="muted small">You can correct the answer key of your own papers only.</p>}
      <div className="grid cols-2">
          {platform && <div className="stack"><h3>Correct one student's marks</h3>
            <Field label="Student ID" value={student} onChange={(e) => { setStudent(e.target.value.trim().toUpperCase()); setSubjectMarks({}); }} placeholder="STU000001" />
            {perSubject ? (
              <>
                {subjects.map((x) => (
                  <Field key={x.subject} label={`${x.subject} marks (out of ${x.total_marks}, now ${x.obtained_marks})`} type="number"
                    value={subjectMarks[x.subject] ?? ""} onChange={(e) => setSubjectMarks({ ...subjectMarks, [x.subject]: e.target.value })} />
                ))}
                <p className="muted small">New total: {subjectTotal}</p>
              </>
            ) : (
              <Field label="Correct marks" type="number" value={marks} onChange={(e) => setMarks(e.target.value)} />
            )}
            <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button busy={act.busy} disabled={!student || reason.trim().length < 5 || (perSubject ? subjects.some((x) => (subjectMarks[x.subject] ?? "") === "") : marks === "")}
              onClick={() => void act.run(async () => {
                const body = perSubject
                  ? { obtained_marks: subjectTotal, reason, subjects: subjects.map((x) => ({ subject: x.subject, obtained_marks: Number(subjectMarks[x.subject]) })) }
                  : { obtained_marks: Number(marks), reason };
                await api(`/api/v1/exams/${examId}/results/${student}/adjustments`, { method: "POST", body, headers: { "Idempotency-Key": adjustKey.key } });
                adjustKey.renew(); setStudent(""); setMarks(""); setSubjectMarks({}); setReason(""); done(); })}>Apply correction</Button></div>}
          <div className="stack"><h3>Correct an answer key and re-evaluate</h3>
            <Field label="Question number" type="number" min={1} value={order} onChange={(e) => setOrder(e.target.value)} />
            <SelectField label="Question type" value={keyType} onChange={(e) => setKeyType(e.target.value as QuestionType)}>
              <option value="MCQ_SINGLE">MCQ (one correct option)</option><option value="MCQ_MULTI">MSQ (one or more correct options)</option><option value="NUMERICAL">NAT (a number)</option>
            </SelectField>
            {keyType === "MCQ_SINGLE" && <SelectField label="Correct option" value={option} onChange={(e) => setOption(e.target.value)}>{["A", "B", "C", "D"].map((o) => <option key={o}>{o}</option>)}</SelectField>}
            {keyType === "MCQ_MULTI" && (
              <fieldset className="field"><legend>Correct options</legend><div className="row">
                {"ABCDEFGH".split("").map((x) => <label key={x} className="row checkbox-field"><input type="checkbox" checked={letters.includes(x)}
                  onChange={() => setLetters(letters.includes(x) ? letters.filter((y) => y !== x) : [...letters, x].sort())} /> {x}</label>)}
              </div></fieldset>
            )}
            {keyType === "NUMERICAL" && (
              <div className="grid cols-2">
                <Field label="Correct answer" value={nat.value} onChange={(e) => setNat({ ...nat, value: e.target.value })} inputMode="decimal" error={nat.value ? numberProblem(nat.value) ?? undefined : undefined} />
                <SelectField label="Accept" value={nat.kind} onChange={(e) => setNat({ ...nat, kind: e.target.value })}>
                  <option value="EXACT">Only this number</option><option value="ABS">± a tolerance</option><option value="RANGE">A range</option></SelectField>
                {nat.kind === "ABS" && <Field label="Tolerance (±)" value={nat.amount} onChange={(e) => setNat({ ...nat, amount: e.target.value })} inputMode="decimal" />}
                {nat.kind === "RANGE" && <><Field label="Minimum" value={nat.min} onChange={(e) => setNat({ ...nat, min: e.target.value })} inputMode="decimal" />
                  <Field label="Maximum" value={nat.max} onChange={(e) => setNat({ ...nat, max: e.target.value })} inputMode="decimal" /></>}
              </div>
            )}
            <TextAreaField label="Reason (required)" value={keyReason} onChange={(e) => setKeyReason(e.target.value)} />
            <p className="muted small">Results a person adjusted by hand are not re-scored; they are listed below as skipped.</p>
            <Button variant="danger" busy={act.busy} disabled={!order || keyReason.trim().length < 5 || !keyReady}
              onClick={() => void act.run(async () => { await api(`/api/v1/exams/${examId}/answer-key-corrections`, { method: "POST", body: { changes: [{ question_order: Number(order), ...change }], reason: keyReason }, headers: { "Idempotency-Key": keyKey.key } }); keyKey.renew(); setOrder(""); setKeyReason(""); done(); })}>Correct key and re-evaluate</Button></div>
      </div>
      {platform && <h3>History</h3>}
      {platform && <DataTable rows={list.data ?? []} rowKey={(c) => c.correction_id} empty={<EmptyState title="No corrections yet" />}
        columns={[{ header: "When", render: (c) => fmt(c.created_at) }, { header: "By", render: (c) => c.actor },
          { header: "What", render: (c) => c.kind === "MARKS_ADJUSTMENT" ? `${c.student_id}: ${c.previous_marks ?? "?"} → ${c.new_marks ?? "?"}`
            : `Key: ${(c.key_changes ?? []).map((k) => `Q${k.question_order} ${k.previous}→${k.new}`).join(", ") || "pending"}; ${c.reevaluation ? `${c.reevaluation.changed.length} result(s) changed${c.reevaluation.skipped_manual.length ? `, ${c.reevaluation.skipped_manual.length} skipped (manual)` : ""}` : "not re-evaluated yet"}` },
          { header: "Reason", render: (c) => c.reason }, { header: "Status", render: (c) => <StatusBadge status={c.status} /> }]} />}
      {(list.data ?? []).some((c) => c.status !== "APPLIED") && <p className="muted small">A correction marked FAILED was saved but not completed. Submit the same correction again to finish it.</p>}
    </Card>
  );
}


type QuestionStat = { question_order: number; question_type: QuestionType; subject: string | null; correct: number; incorrect: number; unanswered: number;
  average_marks: string | null; choices: Record<string, number> | null };

/** Question-level analysis (every type): how each question was answered. Only readers who see every student get it (the server decides). */
function QuestionAnalysisCard({ examId }: { examId: string }) {
  const d = useApi<{ questions: QuestionStat[] }>(`/api/v1/exams/${examId}/results/questions`);
  if (d.error) return null;                                   // not available to this reader: the results above are enough
  return (
    <Card>
      <h2>Question analysis</h2>
      <p className="muted small">How each question was answered across all submitted attempts. An MSQ counts as correct only when exactly the correct options were chosen; its option counts include every option students ticked.</p>
      {d.loading && !d.data ? <Spinner /> : (
        <DataTable rows={d.data?.questions ?? []} rowKey={(x) => String(x.question_order)} empty={<EmptyState title="No submissions yet" />}
          columns={[{ header: "Q", render: (x) => x.question_order }, { header: "Type", render: (x) => <TypeBadge type={x.question_type} /> },
            { header: "Subject", render: (x) => x.subject ?? "—" },
            { header: "Correct", render: (x) => x.correct }, { header: "Incorrect", render: (x) => x.incorrect }, { header: "Unanswered", render: (x) => x.unanswered },
            { header: "Correct %", render: (x) => { const n = x.correct + x.incorrect + x.unanswered; return n ? `${Math.round((100 * x.correct) / n)}%` : "—"; } },
            { header: "Average marks", render: (x) => x.average_marks ?? "—" },
            { header: "Options chosen", render: (x) => (x.choices ? Object.entries(x.choices).sort().map(([k, n]) => `${k}: ${n}`).join(" · ") : "—") }]} />
      )}
    </Card>
  );
}
