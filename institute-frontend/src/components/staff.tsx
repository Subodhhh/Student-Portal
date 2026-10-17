import { useState } from "react";
import { useAuth, type Role } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { activeAssignments, GRANTABLE, isGlobalRole, scopeOf, type Assignment, type Staff } from "../auth/roles";
import { useScope } from "../auth/ScopeContext";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { AuditTable } from "./audit";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, Pagination, SearchField, SelectField, Spinner, StatusBadge, Tabs, TextAreaField } from "./ui";

type StaffListProps = {
  role?: Role;                  // a Role Management category
  providerId?: string;          // staff of one provider (Providers & Branches)
  branchId?: string;            // staff of one branch
  roleFilter?: boolean;         // offer a role filter (provider/branch views)
  providerFilter?: boolean;     // offer a provider filter (global categories of provider-scoped roles)
  addRole?: Role;               // "Add" creates an account with this role
};

/** Searchable, paginated account list with permission-aware actions. Every filter is sent to the API, which applies it on
 * top of the caller's own scope; nothing is filtered or hidden only in the browser. */
export function StaffList({ role, providerId, branchId, roleFilter, providerFilter, addRole }: StaffListProps) {
  const { me, can } = useAuth();
  const scope = useScope();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [pickedRole, setPickedRole] = useState<Role | "">("");
  const [pickedProvider, setPickedProvider] = useState("");
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Staff | null>(null);
  const effectiveRole = role ?? (pickedRole || undefined);
  const params = new URLSearchParams({ page: String(page), page_size: "20" });
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  if (effectiveRole) params.set("role", effectiveRole);
  if (providerId || pickedProvider) params.set("provider_id", providerId || pickedProvider);
  if (branchId) params.set("branch_id", branchId);
  const list = useApi<Page<Staff>>(`/api/v1/staff?${params.toString()}`);
  const canAdd = !!addRole && !!me && can("staff.create") && GRANTABLE[me.primary_role].includes(addRole);
  const reset = () => setPage(1);
  const roleOptions: Role[] = branchId ? ["BRANCH_ADMIN", "TEACHER"] : ["PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"];

  return (
    <div className="stack">
      <div className="toolbar">
        <SearchField label="Search name, email or Staff ID" value={q} onChange={(v) => { setQ(v); reset(); }} />
        {roleFilter && (
          <SelectField label="Role" value={pickedRole} onChange={(e) => { setPickedRole(e.target.value as Role | ""); reset(); }}>
            <option value="">All roles</option>
            {roleOptions.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          </SelectField>
        )}
        {providerFilter && scope.providers.length > 1 && (
          <SelectField label="Provider" value={pickedProvider} onChange={(e) => { setPickedProvider(e.target.value); reset(); }}>
            <option value="">All providers</option>
            {scope.providers.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
          </SelectField>
        )}
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); reset(); }}>
          <option value="">All</option><option value="ACTIVE">Active</option><option value="INACTIVE">Inactive</option>
        </SelectField>
        {canAdd && <Button onClick={() => setCreating(true)}>Add {ROLE_LABEL[addRole!]}</Button>}
      </div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(s) => s.staff_id}
            empty={<EmptyState title={q || status || pickedRole || pickedProvider ? "No accounts match these filters" : "No accounts yet"} />}
            columns={[
              { header: "Staff ID", render: (s) => s.staff_code },
              { header: "Name", render: (s) => s.full_name },
              { header: "Email", render: (s) => <>{s.email}{!s.email_verified && <> <StatusBadge status="PENDING" /></>}</> },
              { header: "Role", render: (s) => activeAssignments(s).map((a) => ROLE_LABEL[a.role]).filter((v, i, xs) => xs.indexOf(v) === i).join(", ") || "—" },
              { header: "Provider", render: (s) => scopeOf(s, effectiveRole).providers.join(", ") || "—" },
              { header: "Branch", render: (s) => scopeOf(s, effectiveRole).branches.join(", ") || "—" },
              { header: "Status", render: (s) => <StatusBadge status={s.status} /> },
              { header: "Last sign-in", render: (s) => (s.last_login_at ? new Date(s.last_login_at).toLocaleDateString() : <span className="muted">never</span>) },
              { header: "Actions", render: (s) => <Button small variant="secondary" onClick={() => setSelected(s)} aria-label={`View ${s.full_name}`}>View</Button> },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
      {creating && addRole && (
        <CreateStaff role={addRole} providerId={providerId} branchId={branchId}
          onClose={() => setCreating(false)} onDone={() => { setCreating(false); list.reload(); }} />
      )}
      {selected && <StaffDetail staff={selected} onClose={() => setSelected(null)} onChanged={() => { list.reload(); setSelected(null); }} />}
    </div>
  );
}

function CreateStaff({ role, providerId: fixedProvider, branchId: fixedBranch, onClose, onDone }:
  { role: Role; providerId?: string; branchId?: string; onClose: () => void; onDone: () => void }) {
  const scope = useScope();
  const [f, setF] = useState({ full_name: "", email: "", phone: "" });
  const [providerId, setProviderId] = useState(fixedProvider ?? "");
  const [branchId, setBranchId] = useState(fixedBranch ?? "");
  const needsProvider = !isGlobalRole(role);
  const needsBranch = role === "BRANCH_ADMIN";
  const branches = useApi<Page<{ branch_id: string; name: string }>>(needsBranch && providerId ? `/api/v1/providers/${providerId}/branches?page_size=100` : null);
  const { busy, error, run } = useAction();
  const body = {
    full_name: f.full_name, email: f.email, phone: f.phone || null,
    assignment: { role, provider_id: needsProvider ? providerId || null : null, branch_id: needsBranch ? branchId || null : null },
  };
  const ready = f.full_name.length >= 2 && !!f.email && (!needsProvider || !!providerId) && (!needsBranch || !!branchId);
  return (
    <Modal title={`Add ${ROLE_LABEL[role]}`} onClose={onClose}>
      <Alert kind="info">They receive an email to verify their address and choose a password. An email that is already registered is never duplicated.</Alert>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Full name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} required />
      <Field label="Email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required />
      <Field label="Phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
      {!needsProvider && <p className="muted small">Platform-wide role: no provider or branch.</p>}
      {needsProvider && !fixedProvider && (
        <SelectField label="Provider" value={providerId} onChange={(e) => { setProviderId(e.target.value); setBranchId(""); }}>
          <option value="">Select…</option>{scope.providers.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
        </SelectField>
      )}
      {needsBranch && !fixedBranch && (
        <SelectField label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} disabled={!providerId}>
          <option value="">Select…</option>{(branches.data?.items ?? []).map((b) => <option key={b.branch_id} value={b.branch_id}>{b.name}</option>)}
        </SelectField>
      )}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!ready} onClick={() => void run(async () => { await api("/api/v1/staff", { method: "POST", body }); onDone(); })}>Create & send invite</Button>
      </div>
    </Modal>
  );
}

/** Account details and the actions the viewer is allowed to take (each one is checked again by the API). */
export function StaffDetail({ staff, onClose, onChanged }: { staff: Staff; onClose: () => void; onChanged: () => void }) {
  const { me, can } = useAuth();
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState<null | { kind: "deactivate" | "reactivate" } | { kind: "remove"; assignment: Assignment }>(null);
  const [notice, setNotice] = useState("");
  const [tab, setTab] = useState("roles");
  const act = useAction();
  if (!me) return null;
  const self = staff.staff_id === me.staff_id;
  const targetIsSuperAdmin = activeAssignments(staff).some((a) => a.role === "SUPER_ADMIN");
  const protectedTarget = targetIsSuperAdmin && me.primary_role !== "SUPER_ADMIN";   // only a Super Admin may touch a Super Admin
  const canRemove = (a: Assignment) => !self && !protectedTarget && a.status === "ACTIVE" && can("staff.assign_role") && GRANTABLE[me.primary_role].includes(a.role);
  const canChangeStatus = !self && !protectedTarget && isGlobalRole(me.primary_role);      // identity-wide: global roles only
  const canResend = !staff.email_verified && !protectedTarget && me.primary_role !== "TEACHER";
  const close = () => { setConfirm(null); setReason(""); };
  const submit = () => act.run(async () => {
    if (!confirm) return;
    if (confirm.kind === "remove") {
      await api(`/api/v1/staff/${staff.staff_id}/assignments/${confirm.assignment.assignment_id}`, { method: "DELETE", body: { reason } });
    } else {
      await api(`/api/v1/staff/${staff.staff_id}/${confirm.kind}`, { method: "POST", body: { reason } });
    }
    close();
    onChanged();
  });
  const scopeLabel = (a: Assignment) => (a.branch_id ? `${a.provider_name ?? "Provider"} / ${a.branch_name ?? "Branch"}` : a.provider_id ? a.provider_name ?? "Another provider" : "Platform");

  return (
    <Modal title={`${staff.full_name} (${staff.staff_code})`} onClose={onClose}>
      <p className="muted small">{staff.email} · <StatusBadge status={staff.status} /> · {staff.email_verified ? "email verified" : "email not verified"}</p>
      <p className="muted small">
        Account created {staff.created_at ? new Date(staff.created_at).toLocaleDateString() : "—"} · last sign-in{" "}
        {staff.last_login_at ? new Date(staff.last_login_at).toLocaleString() : "never recorded"}
      </p>
      {act.error && !confirm && <Alert kind="error">{act.error}</Alert>}
      {notice && <Alert kind="success">{notice}</Alert>}
      <Tabs active={tab} onChange={setTab} tabs={[{ id: "roles", label: "Roles & scope" }, ...(can("audit.read") ? [{ id: "activity", label: "Activity" }] : [])]} />
      {tab === "activity" ? (
        <>
          <h3>Changes to this account</h3>
          <AuditTable filters={{ target_type: "staff", target_id: staff.staff_id }} pageSize={5} emptyTitle="No recorded changes" />
          <h3>Actions by this person</h3>
          <AuditTable filters={{ actor: staff.staff_id }} pageSize={5} emptyTitle="No recorded actions" />
        </>
      ) : <>
      <h3>Role assignments</h3>
      <DataTable rows={staff.assignments} rowKey={(a) => a.assignment_id} empty={<EmptyState title="No role assignments" />}
        columns={[
          { header: "Role", render: (a) => ROLE_LABEL[a.role] },
          { header: "Scope", render: scopeLabel },
          { header: "Status", render: (a) => <StatusBadge status={a.status} /> },
          { header: "Actions", render: (a) => canRemove(a) ? <Button small variant="secondary" onClick={() => setConfirm({ kind: "remove", assignment: a })}>Remove role</Button> : null },
        ]} />
      </>}
      <div className="modal-actions">
        {canResend && <Button small variant="secondary" busy={act.busy} onClick={() => void act.run(async () => { await api(`/api/v1/staff/${staff.staff_id}/resend-verification`, { method: "POST" }); setNotice("Invitation sent again."); })}>Resend invite</Button>}
        {canChangeStatus && (staff.status === "ACTIVE"
          ? <Button small variant="danger" onClick={() => setConfirm({ kind: "deactivate" })}>Deactivate account</Button>
          : <Button small onClick={() => setConfirm({ kind: "reactivate" })}>Reactivate account</Button>)}
        <Button small variant="secondary" onClick={onClose}>Close</Button>
      </div>
      {confirm && (
        <ConfirmDialog
          title={confirm.kind === "remove" ? `Remove ${ROLE_LABEL[confirm.assignment.role]} role` : confirm.kind === "deactivate" ? "Deactivate account" : "Reactivate account"}
          danger={confirm.kind !== "reactivate"}
          confirmLabel={confirm.kind === "remove" ? "Remove role" : confirm.kind === "deactivate" ? "Deactivate" : "Reactivate"}
          message={confirm.kind === "remove" ? "They lose this role's access at their next request. Teaching assignments that depend on a Teacher role end too."
            : confirm.kind === "deactivate" ? "They are signed out immediately and cannot sign in again until reactivated." : "They can sign in again with their existing roles."}
          busy={act.busy} onCancel={close} onConfirm={() => void submit()}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          <TextAreaField label="Reason (required, recorded in the audit log)" value={reason} onChange={(e) => setReason(e.target.value)} />
        </ConfirmDialog>
      )}
    </Modal>
  );
}
