import { useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, PageHeader, Pagination, SelectField, Spinner, StatCard, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { StudentCount } from "../components/StudentCount";

type Exam = { exam_id: string; name: string; status: string; start_at: string | null; end_at: string | null; duration_minutes: number | null; total_questions: number | null; total_marks: string | null;
  missing_subjects: { subject: string | null; reason: string; detail: string }[]; papers: { subject_id: string; source_provider_id: string | null; platform_paper?: boolean; paper_version_id: string; question_count: number }[];
  assigned_students: number; assignment_status?: Record<string, number>; delivery_status: string | null;
  unified_global?: boolean; visible_subject_ids?: string[] | null;   // a provider's view of a unified Global Test exam: its own subjects only
  settings?: { marking?: { mode: string; correct?: number; wrong?: number }; audience_type?: string; global_test_id?: string | null; skipped_duplicates?: number } | null };

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");

export function ExamPlansPage() {
  const [params] = useSearchParams();
  // The Exam Plans page was folded into Test -> Scheduled Tests (activate and generate are there); old links keep working.
  // Global Tests moved to Test -> Global Tests (?tab=global).
  return <Navigate to={params.get("tab") === "global" ? "/tests/global" : "/tests"} replace />;
}

export function ExamsPage() {
  return <NeedBranch>{({ branchId }) => <ExamList branchId={branchId} />}</NeedBranch>;
}

function ExamList({ branchId }: { branchId: string }) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const list = useApi<Page<Exam>>(`/api/v1/branches/${branchId}/exams?page=${page}&page_size=20${status ? `&status=${status}` : ""}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`);
  return (
    <>
      <PageHeader subtitle="Exam instances and their lifecycle: when each is available, its status, the students it is assigned to, and its generation and delivery. Submitted attempts and scores are on the Results page." />
      <div className="toolbar">
        <Field label="Search exams" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Name" />
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All</option><option value="SCHEDULED">Scheduled</option><option value="PUBLISHED">Published</option><option value="CANCELLED">Cancelled</option>
        </SelectField>
      </div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <div className="stack">
          <DataTable rows={list.data?.items ?? []} rowKey={(e) => e.exam_id} empty={<EmptyState title={q || status ? "No exam matches" : "No exams generated yet"} />}
            columns={[
              { header: "Exam", render: (e) => <><Link to={`/exams/${e.exam_id}`}>{e.name}</Link>{e.unified_global && <div className="muted small">Global Test date (one exam per student)</div>}</> },
              { header: "Available", render: (e) => `${fmt(e.start_at)} → ${fmt(e.end_at)}` },
              { header: "Questions", render: (e) => e.total_questions ?? "—" }, { header: "Students", render: (e) => <StudentCount count={e.assigned_students} url={`/api/v1/exams/${e.exam_id}/assignments`} title={`Students assigned to ${e.name}`} context="The students this exam is assigned to that you may see, with their attempt status." /> },
              { header: "Status", render: (e) => <StatusBadge status={e.status} /> }, { header: "Delivery", render: (e) => e.delivery_status ? <StatusBadge status={e.delivery_status} /> : "—" },
              { header: "Gaps", render: (e) => (e.missing_subjects.length ? <StatusBadge status="PARTIAL" /> : "") },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </div>
      )}
    </>
  );
}

export function ExamDetailPage() {
  const { examId = "" } = useParams();
  const { can } = useAuth();
  const exam = useApi<Exam>(`/api/v1/exams/${examId}`);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  const act = useAction();
  if (exam.loading) return <Spinner />;
  if (exam.error || !exam.data) return <Alert kind="error">{exam.error || "Not found"}</Alert>;
  const e = exam.data;
  const partial = !!e.visible_subject_ids;      // unified Global Test, provider view: own subjects and students only, no control actions
  return (
    <>
      <PageHeader crumbs={[<Link key="exams" to="/exams">Exams</Link>, e.name]} title={e.name} subtitle={`${fmt(e.start_at)} → ${fmt(e.end_at)} · ${e.duration_minutes} min shared timer`}
        actions={<><StatusBadge status={e.status} />
          {!partial && e.status === "SCHEDULED" && can("exam.publish") && <Button busy={act.busy} onClick={() => void act.run(async () => { await api(`/api/v1/exams/${examId}/publish`, { method: "POST" }); exam.reload(); })}>Publish to students</Button>}
          {!partial && e.status !== "CANCELLED" && can("exam.schedule") && <Button variant="danger" onClick={() => setCancelling(true)}>Cancel exam</Button>}
          {can("result.read") && e.status === "PUBLISHED" && <Link className="btn secondary" to={`/results/${examId}`}>Results</Link>}</>} />
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <div className="stack">
        {partial && <Alert kind="info">One Global Test exam per student, assembled from each student's own institutes. You see only the subjects your
          institute supplied and your own students; the overall score is visible only to the student and the platform.</Alert>}
        <div className="grid stats">
          <StatCard label="Questions" value={e.total_questions ?? 0} /><StatCard label="Total marks" value={e.total_marks ?? 0} /><StatCard label="Assigned students" value={<StudentCount count={e.assigned_students} url={`/api/v1/exams/${examId}/assignments`} title={`Students assigned to ${e.name}`} context="The students this exam is assigned to that you may see, with their attempt status." />} />
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
