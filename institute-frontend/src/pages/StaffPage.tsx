import { useState } from "react";
import { useAuth, type Role } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { useScope } from "../auth/ScopeContext";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

type Staff = { staff_id: string; staff_code: string; full_name: string; email: string; status: string; email_verified: boolean;
  assignments: { assignment_id: string; role: Role; provider_id: string | null; branch_id: string | null; status: string }[] };

// Roles each actor role may grant (mirrors the backend; the server is authoritative).
const GRANTABLE: Record<Role, Role[]> = {
  SUPER_ADMIN: ["SUPER_ADMIN", "ADMIN", "PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"],
  ADMIN: ["ADMIN", "PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"],
  PROVIDER_ADMIN: ["BRANCH_ADMIN", "TEACHER"], BRANCH_ADMIN: ["TEACHER"], TEACHER: [],
};

export default function StaffPage() {
  const { me, can } = useAuth();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Staff | null>(null);
  const list = useApi<Page<Staff>>(`/api/v1/staff?page=${page}&page_size=20${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  return (
    <>
      <PageHeader title="Staff" subtitle="Global staff identities and their role assignments" actions={can("staff.create") && <Button onClick={() => setCreating(true)}>Add staff</Button>} />
      <div className="stack">
        <Field label="Search name, email or Staff ID" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(s) => s.staff_id} empty={<EmptyState title="No staff in your scope" />}
              columns={[
                { header: "Staff ID", render: (s) => s.staff_code }, { header: "Name", render: (s) => <button className="link-btn" onClick={() => setSelected(s)}>{s.full_name}</button> },
                { header: "Email", render: (s) => <>{s.email} {!s.email_verified && <StatusBadge status="PENDING" />}</> },
                { header: "Roles", render: (s) => s.assignments.filter((a) => a.status === "ACTIVE").map((a) => ROLE_LABEL[a.role]).join(", ") || "—" },
                { header: "Status", render: (s) => <StatusBadge status={s.status} /> },
              ]} />
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </div>
      {creating && me && <CreateStaff grantable={GRANTABLE[me.primary_role]} onClose={() => setCreating(false)} onDone={() => { setCreating(false); list.reload(); }} />}
      {selected && <StaffDetail staff={selected} onClose={() => setSelected(null)} onChanged={() => { list.reload(); setSelected(null); }} />}
    </>
  );
}

function ScopePickers({ role, providerId, branchId, setProvider, setBranch }: { role: Role; providerId: string; branchId: string; setProvider: (v: string) => void; setBranch: (v: string) => void }) {
  const scope = useScope();
  const needsProvider = role !== "SUPER_ADMIN" && role !== "ADMIN";
  const branches = useApi<Page<{ branch_id: string; name: string }>>(needsProvider && providerId && role === "BRANCH_ADMIN" ? `/api/v1/providers/${providerId}/branches?page_size=100` : null);
  if (!needsProvider) return <p className="muted small">Platform-wide role: no provider or branch.</p>;
  return (
    <>
      <SelectField label="Provider" value={providerId} onChange={(e) => { setProvider(e.target.value); setBranch(""); }}>
        <option value="">Select…</option>{scope.providers.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
      </SelectField>
      {role === "BRANCH_ADMIN" && (
        <SelectField label="Branch" value={branchId} onChange={(e) => setBranch(e.target.value)}>
          <option value="">Select…</option>{(branches.data?.items ?? []).map((b) => <option key={b.branch_id} value={b.branch_id}>{b.name}</option>)}
        </SelectField>
      )}
    </>
  );
}

function CreateStaff({ grantable, onClose, onDone }: { grantable: Role[]; onClose: () => void; onDone: () => void }) {
  const scope = useScope();
  const [f, setF] = useState({ full_name: "", email: "", phone: "" });
  const [role, setRole] = useState<Role>(grantable[0] ?? "TEACHER");
  const [providerId, setProviderId] = useState(scope.providerId);
  const [branchId, setBranchId] = useState(scope.branchId);
  const { busy, error, run } = useAction();
  const body = { full_name: f.full_name, email: f.email, phone: f.phone || null, assignment: { role, provider_id: ["SUPER_ADMIN", "ADMIN"].includes(role) ? null : providerId || null, branch_id: role === "BRANCH_ADMIN" ? branchId || null : null } };
  return (
    <Modal title="Add staff member" onClose={onClose}>
      <Alert kind="info">They receive an email to verify their address and choose a password. An existing person is never duplicated: if the email is already registered, use “look up” and add a role instead.</Alert>
      <div style={{ height: "var(--space-3)" }} />
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Full name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} required />
      <Field label="Email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required />
      <Field label="Phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
      <SelectField label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>{grantable.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</SelectField>
      <ScopePickers role={role} providerId={providerId} branchId={branchId} setProvider={setProviderId} setBranch={setBranchId} />
      <Button busy={busy} disabled={f.full_name.length < 2 || !f.email} onClick={() => void run(async () => { await api("/api/v1/staff", { method: "POST", body }); onDone(); })}>Create & send invite</Button>
    </Modal>
  );
}

function StaffDetail({ staff, onClose, onChanged }: { staff: Staff; onClose: () => void; onChanged: () => void }) {
  const { me, can } = useAuth();
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState<null | "deactivate" | "reactivate">(null);
  const act = useAction();
  const isGlobal = me?.primary_role === "SUPER_ADMIN" || me?.primary_role === "ADMIN";
  const end = (assignmentId: string) => act.run(async () => {
    const r = window.prompt("Reason for removing this role (required, recorded in the audit log):");
    if (!r) return;
    await api(`/api/v1/staff/${staff.staff_id}/assignments/${assignmentId}`, { method: "DELETE", body: { reason: r } });
    onChanged();
  });
  return (
    <Modal title={`${staff.full_name} (${staff.staff_code})`} onClose={onClose}>
      <p className="muted small">{staff.email} · <StatusBadge status={staff.status} /> {staff.email_verified ? "· email verified" : "· email not verified"}</p>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <DataTable rows={staff.assignments} rowKey={(a) => a.assignment_id}
        columns={[{ header: "Role", render: (a) => ROLE_LABEL[a.role] }, { header: "Scope", render: (a) => a.branch_id ? "Branch" : a.provider_id ? "Provider" : "Platform" },
          { header: "Status", render: (a) => <StatusBadge status={a.status} /> },
          { header: "", render: (a) => can("staff.assign_role") && a.status === "ACTIVE" && staff.staff_id !== me?.staff_id ? <Button small variant="secondary" onClick={() => void end(a.assignment_id)}>Remove</Button> : null }]} />
      <div className="row" style={{ marginTop: "var(--space-4)" }}>
        {!staff.email_verified && <Button small variant="secondary" onClick={() => void act.run(async () => { await api(`/api/v1/staff/${staff.staff_id}/resend-verification`, { method: "POST" }); window.alert("Invitation re-sent."); })}>Resend invite</Button>}
        {isGlobal && staff.staff_id !== me?.staff_id && (staff.status === "ACTIVE"
          ? <Button small variant="danger" onClick={() => setConfirm("deactivate")}>Deactivate account</Button>
          : <Button small onClick={() => setConfirm("reactivate")}>Reactivate</Button>)}
      </div>
      {confirm && (
        <ConfirmDialog title={confirm === "deactivate" ? "Deactivate account" : "Reactivate account"} danger={confirm === "deactivate"}
          message={confirm === "deactivate" ? "They are signed out immediately and cannot sign in again." : "They can sign in again."}
          busy={act.busy} onCancel={() => setConfirm(null)}
          onConfirm={() => void act.run(async () => { await api(`/api/v1/staff/${staff.staff_id}/${confirm}`, { method: "POST", body: { reason } }); onChanged(); })}>
          <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        </ConfirmDialog>
      )}
    </Modal>
  );
}
