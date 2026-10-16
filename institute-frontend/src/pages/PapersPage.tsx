import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, API_URL, api, type Page } from "../services/api";

type Subject = { subject_id: string; code: string; name: string };
type PV = { paper_version_id: string; paper_id: string; version_no: number; status: string; question_count: number; total_marks: string; created_at: string };
type PaperRow = { paper_id: string; subject_id: string; title: string; latest_version: PV | null };
type Q = { question_version_id: string; version_no: number; stem: string; option_a: string; option_b: string; option_c: string; option_d: string; correct_option: string; marks_positive: string; marks_negative: string; explanation: string | null; topic: string | null };
type Detail = PaperRow & { questions: Q[]; history: { event: string; from: string | null; to: string | null; reason: string | null; at: string }[] };
type UploadError = { row: number | null; field: string | null; message: string };

export function PapersPage() {
  return <NeedBranch>{({ providerId, branchId }) => <Inner providerId={providerId} branchId={branchId} />}</NeedBranch>;
}

const TEMPLATE = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks,explanation,topic\n\"What is the SI unit of force?\",Newton,Joule,Watt,Pascal,A,4,1,\"F = ma\",Mechanics\n";

function Inner({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const list = useApi<Page<PaperRow>>(`/api/v1/branches/${branchId}/papers?page=${page}&page_size=20`);
  const [subjectId, setSubjectId] = useState("");
  const [title, setTitle] = useState("");
  const [errors, setErrors] = useState<UploadError[]>([]);
  const [okMsg, setOkMsg] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const up = useAction();
  const sName = (id: string) => subjects.data?.find((s) => s.subject_id === id)?.name ?? "—";

  const upload = () => up.run(async () => {
    setErrors([]); setOkMsg("");
    const f = file.current?.files?.[0];
    if (!f) throw new Error("Choose a CSV, Excel (.xlsx), Word (.docx) or PDF file first.");
    const form = new FormData();
    form.append("file", f); form.append("title", title);
    try {
      const r = await api<{ question_count: number; version_no: number }>(`/api/v1/branches/${branchId}/subjects/${subjectId}/papers/upload`, { method: "POST", form });
      setOkMsg(`Validated: ${r.question_count} questions imported as version ${r.version_no}. Review it, then approve and publish.`);
      setTitle(""); if (file.current) file.current.value = "";
      list.reload();
    } catch (e) {
      if (e instanceof ApiError && e.code === "EXTRACTION_FAILED") { setErrors((e.extra.errors as UploadError[]) ?? []); throw new Error("The paper was rejected. No questions were imported — fix the rows below and upload again.", { cause: e }); }
      throw e;
    }
  });

  return (
    <>
      <PageHeader title="Question bank & papers" subtitle="Upload, validate, approve and publish versioned papers" />
      <div className="stack">
        {can("paper.upload") && (
          <Card>
            <h2>Upload a paper</h2>
            <p className="muted small">One file per paper: CSV or one-sheet Excel (.xlsx) with the template columns, or a Word (.docx) / text PDF written as numbered questions. Formulas, macros, merged or hidden cells and embedded objects are rejected. Every question is validated; if any question fails, the whole paper is rejected and nothing is imported.{" "}
              <a href={`data:text/csv;charset=utf-8,${encodeURIComponent(TEMPLATE)}`} download="paper-template.csv">Download the template</a></p>
            <details className="small">
              <summary>File format</summary>
              <p>First row = column names (any order, case-insensitive). Required: <code>question</code>, <code>option_a</code>, <code>option_b</code>, <code>option_c</code>, <code>option_d</code>,
                <code> correct</code> (A, B, C or D), <code>marks</code> (positive number), <code>negative_marks</code> (zero or more). Optional: <code>explanation</code>, <code>topic</code>.
                The subject is the one you choose below. Options must be different, questions must not repeat, and one sheet only. Scanned images are accepted only where image extraction is enabled; otherwise they are rejected.</p>
              <p><strong>Word / PDF:</strong> either a table whose first row has the column names above, or numbered questions:</p>
              <pre className="small">{"Default marks: 4\nDefault negative: 1\n\n1. What is the SI unit of force?\nA) Newton\nB) Joule\nC) Watt\nD) Pascal\nAnswer: A\nExplanation: F = ma   (optional)\nTopic: Mechanics      (optional)"}</pre>
              <p>Marks / Negative lines inside a question override the defaults. Numbering must run 1, 2, 3… without gaps. Scanned PDFs (no selectable text) are rejected. A scanned page can be uploaded as PNG/JPEG in the same numbered layout where image extraction is enabled; any question read with low confidence (handwriting, blur) rejects the whole paper.</p>
            </details>
            {up.error && <Alert kind="error">{up.error}</Alert>}
            {okMsg && <Alert kind="success">{okMsg}</Alert>}
            {errors.length > 0 && (
              <DataTable rows={errors} rowKey={(e) => `${e.row}-${e.field}-${e.message}`} columns={[{ header: "Row", render: (e) => e.row ?? "—" }, { header: "Field", render: (e) => e.field ?? "—" }, { header: "Problem", render: (e) => e.message }]} />
            )}
            <div style={{ height: "var(--space-3)" }} />
            <div className="grid cols-2">
              <SelectField label="Subject" value={subjectId} onChange={(e) => setSubjectId(e.target.value)}><option value="">Select…</option>{(subjects.data ?? []).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}</SelectField>
              <Field label="Paper title" value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="field"><label htmlFor="paper-file">CSV, Excel, Word, PDF or scanned image (max 2 MB)</label><input id="paper-file" type="file" accept=".csv,.xlsx,.docx,.pdf,.png,.jpg,.jpeg,text/csv,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf" ref={file} /></div>
            <Button busy={up.busy} disabled={!subjectId || title.length < 3} onClick={() => void upload()}>Validate & import</Button>
          </Card>
        )}
        <Card>
          <h2>Papers in this branch</h2>
          {list.error && <Alert kind="error">{list.error}</Alert>}
          {list.loading ? <Spinner /> : (
            <>
              <DataTable rows={list.data?.items ?? []} rowKey={(p) => p.paper_id} empty={<EmptyState title="No papers yet" hint="Upload the first paper above." />}
                columns={[
                  { header: "Title", render: (p) => p.latest_version ? <Link to={`/papers/${p.latest_version.paper_version_id}`}>{p.title}</Link> : p.title },
                  { header: "Subject", render: (p) => sName(p.subject_id) },
                  { header: "Latest version", render: (p) => p.latest_version ? `v${p.latest_version.version_no} · ${p.latest_version.question_count} questions · ${p.latest_version.total_marks} marks` : "—" },
                  { header: "Status", render: (p) => p.latest_version ? <StatusBadge status={p.latest_version.status} /> : "—" },
                ]} />
              <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
            </>
          )}
        </Card>
      </div>
    </>
  );
}

export function PaperDetailPage() {
  const { versionId = "" } = useParams();
  const { me } = useAuth();
  const d = useApi<Detail>(`/api/v1/paper-versions/${versionId}`);
  const [confirm, setConfirm] = useState<null | "approve" | "publish" | "withdraw" | "override">(null);
  const [reason, setReason] = useState("");
  const [toStatus, setToStatus] = useState("PUBLISHED");
  const [editing, setEditing] = useState<{ position: number; q: Q } | null>(null);
  const { can } = useAuth();
  const navigate = useNavigate();
  const act = useAction();
  if (d.loading) return <Spinner />;
  if (d.error || !d.data) return <Alert kind="error">{d.error || "Not found"}</Alert>;
  const v = d.data.latest_version!;
  const run = () => act.run(async () => {
    const body = confirm === "override" ? { to_status: toStatus, reason } : confirm === "withdraw" ? { reason } : { note: reason || null };
    await api(`/api/v1/paper-versions/${versionId}/${confirm === "override" ? "override-status" : confirm}`, { method: "POST", body });
    setConfirm(null); setReason(""); d.reload();
  });
  return (
    <>
      <PageHeader crumbs={["Papers", d.data.title]} title={`${d.data.title} — v${v.version_no}`} subtitle={`${v.question_count} questions · ${v.total_marks} marks`}
        actions={<><StatusBadge status={v.status} />
          {v.status === "VALIDATED" && <Button onClick={() => setConfirm("approve")}>Approve</Button>}
          {v.status === "APPROVED" && <Button onClick={() => setConfirm("publish")}>Publish</Button>}
          {(v.status === "VALIDATED" || v.status === "APPROVED" || v.status === "PUBLISHED") && <Button variant="danger" onClick={() => setConfirm("withdraw")}>{v.status === "VALIDATED" ? "Reject draft" : "Withdraw"}</Button>}
          {me?.primary_role === "SUPER_ADMIN" && <Button variant="secondary" onClick={() => setConfirm("override")}>Override status</Button>}</>} />
      <Alert kind="info">{v.status === "VALIDATED" ? "This is a draft extracted from your file. Check every question, correct anything that is wrong (a correction saves a new version, nothing is overwritten), then approve and publish." : "Versions are immutable. Exams already generated keep the exact version they used; a correction creates the next version, which you approve and publish again."}</Alert>
      <div style={{ height: "var(--space-4)" }} />
      <div className="stack">
        <Card>
          <h2>Questions (preview — visible to authorized staff only)</h2>
          {d.data.questions.map((q, i) => (
            <article key={q.question_version_id} style={{ borderBottom: "1px solid var(--c-border)", padding: "var(--space-3) 0" }}>
              <div className="row between"><strong>{i + 1}. {q.stem}</strong>
                {can("paper.create") && v.status !== "WITHDRAWN" && <Button variant="secondary" small onClick={() => setEditing({ position: i + 1, q })}>Correct</Button>}</div>
              <span className="muted small">(+{q.marks_positive} / −{q.marks_negative}{q.topic ? ` · ${q.topic}` : ""})</span>
              <ol type="A" style={{ margin: "6px 0" }}>{[q.option_a, q.option_b, q.option_c, q.option_d].map((o, k) => <li key={k} style={q.correct_option === "ABCD"[k] ? { fontWeight: 700, color: "var(--c-ok)" } : undefined}>{o}</li>)}</ol>
              {q.explanation && <p className="small muted">Explanation: {q.explanation}</p>}
            </article>
          ))}
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
  const [f, setF] = useState({ stem: q.stem, option_a: q.option_a, option_b: q.option_b, option_c: q.option_c, option_d: q.option_d, correct_option: q.correct_option,
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
