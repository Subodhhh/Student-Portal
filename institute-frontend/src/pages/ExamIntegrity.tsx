import { useState } from "react";
import { Alert, Badge, Button, Card, DataTable, EmptyState, Modal, Spinner, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";

// Exam integrity: attempts of the caller's students with browser warnings or an automatic submission after warnings, the student's
// review request, and the decision (a NEW authorized retake, or upholding the submission). The API scopes every row; deciding is
// for the Platform Admin or the student's Provider Admin (can_decide). Warnings are browser evidence, not proof of cheating.

type Review = { status: string; student_message: string; requested_at?: string; decided_at?: string | null; decided_by_name?: string | null;
  decision_reason?: string | null } | null;
type Row = { student_id: string; name: string | null; attempt_id: string; status: string; submit_reason: string | null; review_status: string | null;
  warnings: number; retake_of_attempt_id: string | null; review: Review; can_decide: boolean };
type Detail = Row & { threshold: number; started_at: string; submitted_at: string | null;
  events: { kind: string; received_at: string; counted: boolean; warning_no: number | null }[];
  retake: { attempt_id: string; status: string; started_at: string; submitted_at: string | null } | null };

const KIND: Record<string, string> = { TAB_HIDDEN: "Left the test tab", WINDOW_BLUR: "Window lost focus", FULLSCREEN_EXIT: "Left fullscreen" };
const REVIEW: Record<string, string> = { UNDER_REVIEW: "Under review", RETAKE_AUTHORIZED: "Retake authorized", UPHELD: "Submission upheld" };
const fmt = (s: string | null | undefined) => (s ? new Date(s).toLocaleString() : "—");

export function IntegrityCard({ examId }: { examId: string }) {
  const [open, setOpen] = useState(false);
  const list = useApi<{ items: Row[] }>(open ? `/api/v1/exams/${examId}/integrity` : null);
  const [viewing, setViewing] = useState<string | null>(null);
  return (
    <Card>
      <div className="row between"><h2>Exam integrity</h2>{!open && <Button variant="secondary" onClick={() => setOpen(true)}>Load warnings</Button>}</div>
      <p className="muted small">Browser warnings (leaving the test tab or window, or leaving fullscreen) and automatic submissions after the warning
        limit. They record what the browser reported; they are not proof of cheating.</p>
      {list.loading && <Spinner />}{list.error && <Alert kind="error">{list.error}</Alert>}
      {list.data && (
        <DataTable rows={list.data.items} rowKey={(r) => r.attempt_id} empty={<EmptyState title="No warnings recorded for your students" />}
          columns={[
            { header: "Student", render: (r) => <>{r.name ?? "—"}<div className="muted small">{r.student_id}</div></> },
            { header: "Attempt", render: (r) => (r.retake_of_attempt_id ? <Badge tone="info">Authorized retake</Badge> : "Original") },
            { header: "Warnings", render: (r) => r.warnings },
            { header: "Submission", render: (r) => (r.submit_reason === "WARNING_THRESHOLD" ? <Badge tone="warn">Automatic (warning limit)</Badge> : r.status) },
            { header: "Review", render: (r) => <>{r.review_status ? REVIEW[r.review_status] ?? r.review_status : "—"}{r.review?.status === "OPEN" && <div className="small"><Badge tone="warn">Student asked for a review</Badge></div>}</> },
            { header: "", render: (r) => <Button small variant="secondary" onClick={() => setViewing(r.attempt_id)}>Investigate</Button> },
          ]} />
      )}
      {viewing && <Investigate examId={examId} attemptId={viewing} onClose={() => setViewing(null)} onDecided={() => list.reload()} />}
    </Card>
  );
}

function Investigate({ examId, attemptId, onClose, onDecided }: { examId: string; attemptId: string; onClose: () => void; onDecided: () => void }) {
  const d = useApi<Detail>(`/api/v1/exams/${examId}/integrity/${attemptId}`);
  const [reason, setReason] = useState("");
  const act = useAction();
  const decide = (decision: "RETAKE" | "UPHOLD") => void act.run(async () => {
    await api(`/api/v1/exams/${examId}/integrity/${attemptId}/decision`, { method: "POST", body: { decision, reason } });
    d.reload();
    onDecided();
  });
  const x = d.data;
  return (
    <Modal title="Investigate attempt" onClose={onClose}>
      {d.loading && <Spinner />}{d.error && <Alert kind="error">{d.error}</Alert>}
      {x && (
        <div className="stack">
          <dl className="facts">
            <dt>Student</dt><dd>{x.student_id}</dd>
            <dt>Attempt</dt><dd>Started {fmt(x.started_at)} · submitted {fmt(x.submitted_at)} ({x.submit_reason === "WARNING_THRESHOLD" ? "automatically, warning limit reached" : x.submit_reason ?? x.status})</dd>
            <dt>Warnings</dt><dd>{x.warnings} of {x.threshold}</dd>
            {x.review && <><dt>Student's request</dt><dd>“{x.review.student_message}” <span className="muted small">{fmt(x.review.requested_at)}</span></dd></>}
            {x.review && x.review.status !== "OPEN" && <><dt>Decision</dt><dd>{REVIEW[x.review.status] ?? x.review.status} by {x.review.decided_by_name} on {fmt(x.review.decided_at)}: {x.review.decision_reason}</dd></>}
            {x.retake && <><dt>Retake</dt><dd>{x.retake.status} · started {fmt(x.retake.started_at)}</dd></>}
          </dl>
          <DataTable rows={x.events} rowKey={(e) => e.received_at + e.kind} empty={<EmptyState title="No browser events recorded" />}
            columns={[{ header: "When (server time)", render: (e) => fmt(e.received_at) }, { header: "Event", render: (e) => KIND[e.kind] ?? e.kind },
                      { header: "Counted", render: (e) => (e.counted ? `Warning ${e.warning_no}` : "Same action (not counted)") }]} />
          {x.submit_reason === "WARNING_THRESHOLD" && (!x.review || x.review.status === "OPEN") && (
            x.can_decide ? (
              <>
                <p className="small">Authorizing a retake gives the student <strong>one new, separate attempt</strong>; this attempt, its answers and
                  its result are kept permanently. The retake counts officially once it is submitted.</p>
                <TextAreaField label="Reason (required, recorded)" value={reason} onChange={(e) => setReason(e.target.value)} />
                {act.error && <Alert kind="error">{act.error}</Alert>}
                <div className="row">
                  <Button busy={act.busy} disabled={reason.trim().length < 5} onClick={() => decide("RETAKE")}>Authorize a retake</Button>
                  <Button variant="secondary" busy={act.busy} disabled={reason.trim().length < 5} onClick={() => decide("UPHOLD")}>Uphold the submission</Button>
                </div>
              </>
            ) : <p className="muted small">Only the Platform Admin or the student's Provider Admin can decide this review.</p>
          )}
        </div>
      )}
    </Modal>
  );
}
