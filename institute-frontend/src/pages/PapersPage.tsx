import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { paperActions } from "../auth/roles";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, API_URL, api } from "../services/api";

type PV = { paper_version_id: string; paper_id: string; version_no: number; status: string; question_count: number; total_marks: string; created_at: string; published_at?: string | null };
type PaperRow = { paper_id: string; scope?: string; subject_id: string; title: string; latest_version: PV | null };
type Q = { question_version_id: string; version_no: number; stem: string; option_a: string; option_b: string; option_c: string; option_d: string; correct_option: string | null; marks_positive: string; marks_negative: string; explanation: string | null; topic: string | null; answers_hidden?: boolean };
type Detail = PaperRow & { questions: Q[]; history: { event: string; from: string | null; to: string | null; reason: string | null; at: string }[] };
type UploadError = { row: number | null; field: string | null; message: string };

export function PaperDetailPage() {
  const { versionId = "" } = useParams();
  const { me } = useAuth();
  const d = useApi<Detail>(`/api/v1/paper-versions/${versionId}`);
  const versions = useApi<PV[]>(d.data ? `/api/v1/papers/${d.data.paper_id}/versions` : null);
  const [confirm, setConfirm] = useState<null | "approve" | "publish" | "withdraw" | "override">(null);
  const [reason, setReason] = useState("");
  const [toStatus, setToStatus] = useState("PUBLISHED");
  const [editing, setEditing] = useState<{ position: number; q: Q } | null>(null);
  const navigate = useNavigate();
  const act = useAction();
  if (d.loading) return <Spinner />;
  if (d.error || !d.data) return <Alert kind="error">{d.error || "Not found"}</Alert>;
  const v = d.data.latest_version!;
  const allow = paperActions(me, d.data.scope);
  const run = () => act.run(async () => {
    const body = confirm === "override" ? { to_status: toStatus, reason } : confirm === "withdraw" ? { reason } : { note: reason || null };
    await api(`/api/v1/paper-versions/${versionId}/${confirm === "override" ? "override-status" : confirm}`, { method: "POST", body });
    setConfirm(null); setReason(""); d.reload();
  });
  return (
    <>
      <PageHeader crumbs={[<Link key="papers" to="/papers">Question Bank & Papers</Link>, d.data.title]} title={`${d.data.title} — v${v.version_no}`} subtitle={`${v.question_count} questions · ${v.total_marks} marks`}
        actions={<><StatusBadge status={v.status} />
          {allow.approve && v.status === "VALIDATED" && <Button onClick={() => setConfirm("approve")}>Approve</Button>}
          {allow.publish && v.status === "APPROVED" && <Button onClick={() => setConfirm("publish")}>Publish</Button>}
          {allow.withdraw && (v.status === "VALIDATED" || v.status === "APPROVED" || v.status === "PUBLISHED") && <Button variant="danger" onClick={() => setConfirm("withdraw")}>{v.status === "VALIDATED" ? "Reject draft" : "Withdraw"}</Button>}
          {allow.override && <Button variant="secondary" onClick={() => setConfirm("override")}>Override status</Button>}</>} />
      <Alert kind="info">{v.status === "VALIDATED" ? "This is a draft extracted from your file. Check every question, correct anything that is wrong (a correction saves a new version, nothing is overwritten), then approve and publish." : "Versions are immutable. Exams already generated keep the exact version they used; a correction creates the next version, which you approve and publish again."}</Alert>
      <div style={{ height: "var(--space-4)" }} />
      <div className="stack">
        <Card>
          <h2>Questions (preview — visible to authorized staff only)</h2>
          {d.data.questions.map((q, i) => (
            <article key={q.question_version_id} style={{ borderBottom: "1px solid var(--c-border)", padding: "var(--space-3) 0" }}>
              <div className="row between"><strong>{i + 1}. {q.stem}</strong>
                {allow.correct && v.status !== "WITHDRAWN" && <Button variant="secondary" small onClick={() => setEditing({ position: i + 1, q })}>Correct</Button>}</div>
              <span className="muted small">(+{q.marks_positive} / −{q.marks_negative}{q.topic ? ` · ${q.topic}` : ""})</span>
              <ol type="A" style={{ margin: "6px 0" }}>{[q.option_a, q.option_b, q.option_c, q.option_d].map((o, k) => <li key={k} style={q.correct_option === "ABCD"[k] ? { fontWeight: 700, color: "var(--c-ok)" } : undefined}>{o}</li>)}</ol>
              {q.explanation && <p className="small muted">Explanation: {q.explanation}</p>}
              {q.answers_hidden && <p className="small muted">Answer and explanation are hidden until your students' review window opens.</p>}
            </article>
          ))}
        </Card>
        <Card>
          <h2>Versions</h2>
          <p className="muted small">{d.data.scope === "GLOBAL" ? "Platform paper: providers may use it; only platform administrators change it." : "Exams always keep the exact version they were generated with."}</p>
          <DataTable rows={versions.data ?? []} rowKey={(x) => x.paper_version_id} empty={versions.loading ? <Spinner /> : <EmptyState title="No versions" />}
            columns={[
              { header: "Version", render: (x) => (x.paper_version_id === versionId ? <strong>v{x.version_no} (this one)</strong> : <Link to={`/papers/${x.paper_version_id}`}>v{x.version_no}</Link>) },
              { header: "Questions", render: (x) => x.question_count }, { header: "Marks", render: (x) => Number(x.total_marks) },
              { header: "Created", render: (x) => new Date(x.created_at).toLocaleString() }, { header: "Status", render: (x) => <StatusBadge status={x.status} /> },
            ]} />
        </Card>
        <Card>
          <h2>History</h2>
          <DataTable rows={d.data.history} rowKey={(h) => h.at + h.event} columns={[{ header: "When", render: (h) => new Date(h.at).toLocaleString() }, { header: "Event", render: (h) => h.event }, { header: "From → to", render: (h) => `${h.from ?? "—"} → ${h.to ?? "—"}` }, { header: "Reason", render: (h) => h.reason ?? "" }]} />
        </Card>
      </div>
      {confirm && (
        <ConfirmDialog title={`${confirm[0].toUpperCase()}${confirm.slice(1)} this version`} message={confirm === "override" ? "Super Admin override. Your reason is recorded permanently." : confirm === "withdraw" ? "Withdrawn papers cannot be used in new exams." : "This action is recorded in the paper history."}
          danger={confirm === "withdraw"} busy={act.busy} onCancel={() => setConfirm(null)} onConfirm={() => void run()}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          {confirm === "override" && <SelectField label="New status" value={toStatus} onChange={(e) => setToStatus(e.target.value)}>{["VALIDATED", "APPROVED", "PUBLISHED", "WITHDRAWN"].map((s) => <option key={s}>{s}</option>)}</SelectField>}
          <TextAreaField label={confirm === "override" || confirm === "withdraw" ? "Reason (required)" : "Note (optional)"} value={reason} onChange={(e) => setReason(e.target.value)} />
        </ConfirmDialog>
      )}
      {editing && <CorrectQuestion versionId={versionId} position={editing.position} q={editing.q} onClose={() => setEditing(null)}
        onSaved={(newVersionId) => { setEditing(null); navigate(`/papers/${newVersionId}`); }} />}
      <span className="sr-only">{API_URL}</span>
    </>
  );
}


function CorrectQuestion({ versionId, position, q, onClose, onSaved }: { versionId: string; position: number; q: Q; onClose: () => void; onSaved: (newVersionId: string) => void }) {
  const act = useAction();
  const [f, setF] = useState({ stem: q.stem, option_a: q.option_a, option_b: q.option_b, option_c: q.option_c, option_d: q.option_d, correct_option: q.correct_option ?? "A",
    marks_positive: String(q.marks_positive), marks_negative: String(q.marks_negative), explanation: q.explanation ?? "", topic: q.topic ?? "" });
  const [reason, setReason] = useState("");
  const [problems, setProblems] = useState<UploadError[]>([]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const submit = () => act.run(async () => {
    setProblems([]);
    // Only fields that actually changed are sent; the server re-validates the whole paper.
    const edit: Record<string, unknown> = { position };
    (Object.keys(f) as (keyof typeof f)[]).forEach((k) => {
      const before = k === "explanation" || k === "topic" ? (q[k] ?? "") : String(q[k as keyof Q]);
      if (f[k] !== before) edit[k] = f[k];
    });
    try {
      const r = await api<{ paper_version_id: string }>(`/api/v1/paper-versions/${versionId}/revisions`, { method: "POST", body: { edits: [edit], reason } });
      onSaved(r.paper_version_id);
    } catch (e) {
      if (e instanceof ApiError && e.code === "REVISION_INVALID") { setProblems((e.extra.errors as UploadError[]) ?? []); throw new Error("The corrected paper is not valid. Nothing was saved.", { cause: e }); }
      throw e;
    }
  });
  return (
    <Modal title={`Correct question ${position}`} onClose={onClose}>
      <p className="small muted">Saving creates the next version of this paper; this version stays exactly as it is.</p>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {problems.length > 0 && <DataTable rows={problems} rowKey={(e) => `${e.row}-${e.field}-${e.message}`} columns={[{ header: "Question", render: (e) => e.row ?? "—" }, { header: "Field", render: (e) => e.field ?? "—" }, { header: "Problem", render: (e) => e.message }]} />}
      <TextAreaField label="Question" value={f.stem} onChange={set("stem")} />
      <div className="grid cols-2">
        {(["option_a", "option_b", "option_c", "option_d"] as const).map((k) => <Field key={k} label={`Option ${k.slice(-1).toUpperCase()}`} value={f[k]} onChange={set(k)} />)}
      </div>
      <div className="grid cols-3">
        <SelectField label="Correct answer" value={f.correct_option} onChange={set("correct_option")}>{["A", "B", "C", "D"].map((o) => <option key={o}>{o}</option>)}</SelectField>
        <Field label="Marks" value={f.marks_positive} onChange={set("marks_positive")} inputMode="decimal" />
        <Field label="Negative marks" value={f.marks_negative} onChange={set("marks_negative")} inputMode="decimal" />
      </div>
      <Field label="Topic (optional)" value={f.topic} onChange={set("topic")} />
      <TextAreaField label="Explanation (optional)" value={f.explanation} onChange={set("explanation")} />
      <TextAreaField label="Why are you correcting this? (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={act.busy} disabled={reason.trim().length < 5} onClick={() => void submit()}>Save as new version</Button>
      </div>
    </Modal>
  );
}
