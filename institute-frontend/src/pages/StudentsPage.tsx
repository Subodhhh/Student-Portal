import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api, type Page } from "../services/api";

type Enrollment = { enrollment_id: string; student_id: string; student_name: string | null; status: string; batch_ids: string[]; enrolled_at: string };
type Batch = { batch_id: string; name: string; status: string };
type Transfer = { transfer_id: string; student_id: string; to_branch_id: string; status: string; reason: string | null };

export default function StudentsPage() {
  return <NeedBranch>{({ providerId, branchId }) => <Inner providerId={providerId} branchId={branchId} />}</NeedBranch>;
}

function Inner({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [enrolling, setEnrolling] = useState(false);
  const [moving, setMoving] = useState<Enrollment | null>(null);
  const [managing, setManaging] = useState<Enrollment | null>(null);
  const list = useApi<Page<Enrollment>>(`/api/v1/branches/${branchId}/enrollments?page=${page}&page_size=25`);
  const batches = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const transfers = useApi<Page<Transfer>>(can("enrollment.transfer_approve") ? `/api/v1/providers/${providerId}/transfers?status=PENDING` : null);
  const batchName = (id: string) => batches.data?.items.find((b) => b.batch_id === id)?.name ?? id.slice(0, 8);
  const decide = useAction();
  return (
    <>
      <PageHeader title="Students & enrollments" subtitle="One global student identity; enrolled per branch and batch" actions={<Button onClick={() => setEnrolling(true)}>Enroll student</Button>} />
      <div className="stack">
        {(transfers.data?.items.length ?? 0) > 0 && (
          <Alert kind="warn">
            <strong>{transfers.data?.items.length} transfer request(s) await your approval</strong>
            {decide.error && <div>{decide.error}</div>}
            {transfers.data?.items.map((t) => (
              <div key={t.transfer_id} className="row" style={{ marginTop: 6 }}>
                <span>{t.student_id}: {t.reason}</span>
                <Button small onClick={() => void decide.run(async () => { await api(`/api/v1/transfers/${t.transfer_id}/approve`, { method: "POST", body: { note: "Approved by provider admin" } }); transfers.reload(); list.reload(); })}>Approve</Button>
                <Button small variant="secondary" onClick={() => void decide.run(async () => { await api(`/api/v1/transfers/${t.transfer_id}/reject`, { method: "POST", body: { note: "Rejected by provider admin" } }); transfers.reload(); })}>Reject</Button>
              </div>
            ))}
          </Alert>
        )}
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(e) => e.enrollment_id} empty={<EmptyState title="No students enrolled" hint="Enroll your first student." />}
              columns={[
                { header: "Student ID", render: (e) => e.student_id }, { header: "Name", render: (e) => e.student_name ?? "—" },
                { header: "Batches", render: (e) => e.batch_ids.map(batchName).join(", ") || "—" },
                { header: "Status", render: (e) => <StatusBadge status={e.status} /> },
                { header: "", render: (e) => (
                  <div className="row">
                    <Button small variant="secondary" onClick={() => setManaging(e)}>Manage</Button>
                    {e.status === "ACTIVE" && <Button small variant="secondary" onClick={() => setMoving(e)}>Request transfer</Button>}
                  </div>
                ) },
              ]} />
            <Pagination page={page} pageSize={25} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </div>
      {enrolling && <EnrollDialog branchId={branchId} batches={batches.data?.items ?? []} onClose={() => setEnrolling(false)} onDone={() => { setEnrolling(false); list.reload(); }} />}
      {managing && <ManageDialog enrollment={managing} batches={batches.data?.items ?? []} onClose={() => setManaging(null)} onChanged={() => { list.reload(); }} />}
      {moving && <TransferDialog enrollment={moving} providerId={providerId} currentBranch={branchId} onClose={() => setMoving(null)} onDone={() => { setMoving(null); transfers.reload(); }} />}
    </>
  );
}

function EnrollDialog({ branchId, batches, onClose, onDone }: { branchId: string; batches: Batch[]; onClose: () => void; onDone: () => void }) {
  const [mode, setMode] = useState<"search" | "new">("search");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [matches, setMatches] = useState<{ student_id: string; name: string }[] | null>(null);
  const [picked, setPicked] = useState("");
  const [nf, setNf] = useState({ first_name: "", last_name: "", email: "", phone: "" });
  const [confirmDistinct, setConfirmDistinct] = useState(false);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [conflict, setConflict] = useState("");
  const search = useAction();
  const submit = useAction();
  const toggle = (id: string) => setBatchIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  const doEnroll = () => submit.run(async () => {
    setConflict("");
    try {
      const body = mode === "search" ? { student_id: picked, batch_ids: batchIds } : { new_student: { ...nf, phone: nf.phone || null }, confirm_distinct_person: confirmDistinct, batch_ids: batchIds };
      await api(`/api/v1/branches/${branchId}/enrollments`, { method: "POST", body });
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.code === "STUDENT_MATCH_FOUND") { setConflict(e.message); return; }
      throw e;
    }
  });
  return (
    <Modal title="Enroll a student" onClose={onClose}>
      <div className="tabs"><button aria-selected={mode === "search"} onClick={() => setMode("search")}>Existing student</button><button aria-selected={mode === "new"} onClick={() => setMode("new")}>New student</button></div>
      {mode === "search" ? (
        <>
          <p className="muted small">Search the global registry first so nobody gets a second Student ID.</p>
          <div className="grid cols-2"><Field label="Email" value={email} onChange={(e) => setEmail(e.target.value)} /><Field label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} /></div>
          {search.error && <Alert kind="error">{search.error}</Alert>}
          <Button variant="secondary" busy={search.busy} disabled={!email && !phone} onClick={() => void search.run(async () => { const r = await api<{ matches: { student_id: string; name: string }[] }>("/api/v1/students/lookup", { method: "POST", body: { branch_id: branchId, email: email || null, phone: phone || null } }); setMatches(r.matches); setPicked(r.matches[0]?.student_id ?? ""); })}>Search</Button>
          {matches && (matches.length === 0 ? <Alert kind="info">No match. Use “New student”.</Alert> : (
            <div role="radiogroup" aria-label="Matches" style={{ margin: "var(--space-3) 0" }}>
              {matches.map((m) => <label key={m.student_id} className="row"><input type="radio" name="m" checked={picked === m.student_id} onChange={() => setPicked(m.student_id)} />{m.student_id} — {m.name}</label>)}
            </div>
          ))}
        </>
      ) : (
        <>
          <div className="grid cols-2"><Field label="First name" value={nf.first_name} onChange={(e) => setNf({ ...nf, first_name: e.target.value })} /><Field label="Last name" value={nf.last_name} onChange={(e) => setNf({ ...nf, last_name: e.target.value })} /></div>
          <Field label="Email" type="email" value={nf.email} onChange={(e) => setNf({ ...nf, email: e.target.value })} />
          <Field label="Phone" value={nf.phone} onChange={(e) => setNf({ ...nf, phone: e.target.value })} />
          {conflict && <Alert kind="warn">{conflict}<label className="row"><input type="checkbox" checked={confirmDistinct} onChange={(e) => setConfirmDistinct(e.target.checked)} /> This is a different person</label></Alert>}
        </>
      )}
      <h3 style={{ marginTop: "var(--space-4)" }}>Batches</h3>
      {batches.filter((b) => b.status === "ACTIVE").map((b) => <label key={b.batch_id} className="row"><input type="checkbox" checked={batchIds.includes(b.batch_id)} onChange={() => toggle(b.batch_id)} />{b.name}</label>)}
      {submit.error && <Alert kind="error">{submit.error}</Alert>}
      <div style={{ height: "var(--space-3)" }} />
      <Button busy={submit.busy} onClick={() => void doEnroll()} disabled={mode === "search" ? !picked : !(nf.first_name && nf.last_name && nf.email)}>Enroll</Button>
    </Modal>
  );
}

function TransferDialog({ enrollment, providerId, currentBranch, onClose, onDone }: { enrollment: Enrollment; providerId: string; currentBranch: string; onClose: () => void; onDone: () => void }) {
  const branches = useApi<Page<{ branch_id: string; name: string }>>(`/api/v1/providers/${providerId}/branches?page_size=100`);
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const { busy, error, run } = useAction();
  return (
    <ConfirmDialog title={`Transfer ${enrollment.student_id}`} message="A Provider Admin must approve cross-branch transfers. The old enrollment is kept as history." confirmLabel="Request transfer" busy={busy}
      onCancel={onClose} onConfirm={() => void run(async () => { await api(`/api/v1/enrollments/${enrollment.enrollment_id}/transfers`, { method: "POST", body: { to_branch_id: to, reason } }); onDone(); })}>
      {error && <Alert kind="error">{error}</Alert>}
      <SelectField label="Target branch" value={to} onChange={(e) => setTo(e.target.value)}><option value="">Select…</option>{(branches.data?.items ?? []).filter((b) => b.branch_id !== currentBranch).map((b) => <option key={b.branch_id} value={b.branch_id}>{b.name}</option>)}</SelectField>
      <TextAreaField label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
    </ConfirmDialog>
  );
}

/** Batch membership and enrollment status of one student in this branch. Every change is a separate audited API call; the
 * history is kept server-side (enrollment_events), and the Student Portal sees the change after the next reference sync. */
function ManageDialog({ enrollment, batches, onClose, onChanged }: { enrollment: Enrollment; batches: Batch[]; onClose: () => void; onChanged: () => void }) {
  const [current, setCurrent] = useState<Enrollment>(enrollment);
  const [reason, setReason] = useState("");
  const [saved, setSaved] = useState("");
  const { busy, error, run } = useAction();
  const active = current.status === "ACTIVE";
  const toggleBatch = (batchId: string, member: boolean) => void run(async () => {
    setSaved("");
    await api(`/api/v1/enrollments/${current.enrollment_id}/batches/${batchId}`, { method: member ? "DELETE" : "PUT" });
    setCurrent((c) => ({ ...c, batch_ids: member ? c.batch_ids.filter((b) => b !== batchId) : [...c.batch_ids, batchId] }));
    setSaved(member ? "Removed from batch." : "Added to batch.");
    onChanged();
  });
  const setStatus = (status: "ACTIVE" | "INACTIVE") => void run(async () => {
    setSaved("");
    const out = await api<Enrollment>(`/api/v1/enrollments/${current.enrollment_id}`, { method: "PATCH", body: { status, reason } });
    setCurrent((c) => ({ ...c, status: out.status }));
    setReason("");
    setSaved(status === "ACTIVE" ? "Enrollment reactivated." : "Enrollment deactivated.");
    onChanged();
  });
  return (
    <Modal title={`Enrollment of ${current.student_name ?? current.student_id}`} onClose={onClose}>
      <p className="muted small">Student ID {current.student_id} · <StatusBadge status={current.status} /></p>
      {error && <Alert kind="error">{error}</Alert>}
      {saved && <Alert kind="success">{saved}</Alert>}
      <h3>Batches</h3>
      {!active && <p className="muted small">Reactivate the enrollment to change its batches.</p>}
      {batches.filter((b) => b.status === "ACTIVE" || current.batch_ids.includes(b.batch_id)).map((b) => {
        const member = current.batch_ids.includes(b.batch_id);
        return (
          <label key={b.batch_id} className="row">
            <input type="checkbox" checked={member} disabled={busy || (!active && !member) || (!member && b.status !== "ACTIVE")} onChange={() => toggleBatch(b.batch_id, member)} />
            {b.name}{b.status !== "ACTIVE" ? " (inactive batch)" : ""}
          </label>
        );
      })}
      <h3 style={{ marginTop: "var(--space-4)" }}>{active ? "Deactivate enrollment" : "Reactivate enrollment"}</h3>
      <p className="muted small">{active ? "The student stops receiving new exams from this branch. Past attempts and results are kept." : "The student becomes eligible for future exams of this branch again."}</p>
      <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button variant={active ? "danger" : "primary"} busy={busy} disabled={reason.trim().length < 5} onClick={() => setStatus(active ? "INACTIVE" : "ACTIVE")}>
        {active ? "Deactivate" : "Reactivate"}
      </Button>
    </Modal>
  );
}
