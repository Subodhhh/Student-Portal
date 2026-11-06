import { useState } from "react";
import { useAuth, type Role } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { activeAssignments, GRANTABLE, isGlobalRole, scopeOf, type Assignment, type Staff } from "../auth/roles";
import { useScope } from "../auth/ScopeContext";
import { isValidEmail, isValidPhone } from "../auth/validation";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { AuditTable } from "./audit";
import { 
  Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, 
  Pagination, SearchField, SelectField, Spinner, StatusBadge, Tabs, TextAreaField 
} from "./ui";

type StaffListProps = {
  role?: Role;                  
  providerId?: string;          
  branchId?: string;            
  roleFilter?: boolean;         
  providerFilter?: boolean;     
  addRole?: Role;               
};

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
      {/* TOOLBAR */}
      <Card>
        <div className="toolbar" style={{ marginBottom: 0, paddingBottom: 0, borderBottom: "none", flexWrap: "wrap", gap: "1rem" }}>
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
            <option value="">All</option>
            <option value="ACTIVE">Active</option>
            <option value="INACTIVE">Inactive</option>
          </SelectField>

          <div style={{ flex: 1 }} /> 
          
          {canAdd && (
            <Button onClick={() => setCreating(true)}>+ Add {ROLE_LABEL[addRole!]}</Button>
          )}
        </div>
      </Card>

      {list.error && <Alert kind="error">{list.error}</Alert>}
      
      {list.loading && !list.data ? <Spinner /> : (
        <Card className="p-0 overflow-hidden">
          <DataTable 
            rows={list.data?.items ?? []} 
            rowKey={(s) => s.staff_id}
            empty={<EmptyState title={q || status || pickedRole || pickedProvider ? "No accounts match these filters" : "No accounts yet"} />}
            columns={[
              { 
                header: "Staff Member", 
                render: (s) => (
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <button 
                      onClick={() => setSelected(s)}
                      style={{ 
                        fontWeight: 600, color: "var(--c-primary)", textDecoration: "none", 
                        background: "none", border: "none", padding: 0, cursor: "pointer", textAlign: "left" 
                      }}
                    >
                      {s.full_name}
                    </button>
                    <span style={{ fontSize: "0.75rem", color: "var(--c-muted)", fontFamily: "monospace" }}>
                      {s.staff_code}
                    </span>
                  </div>
                ) 
              },
              { 
              header: "Contact & Status", 
              render: (s) => (
                <div style={{ display: "flex", flexDirection: "column", gap: "4px", alignItems: "flex-start" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    {s.email}
                    {!s.email_verified && <StatusBadge status="PENDING" />}
                  </div>
                  <StatusBadge status={s.status} />
                </div>
              ) 
            },
              { 
                header: "Role & Scope", 
                render: (s) => (
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <span style={{ fontWeight: 500 }}>
                      {activeAssignments(s).map((a) => ROLE_LABEL[a.role]).filter((v, i, xs) => xs.indexOf(v) === i).join(", ") || "—"}
                    </span>
                    <span className="muted small">
                      {scopeOf(s, effectiveRole).providers.join(", ") || scopeOf(s, effectiveRole).branches.join(", ") || "Platform"}
                    </span>
                  </div>
                ) 
              },
              { 
                header: "Last Sign-in", 
                render: (s) => (
                  <span className="muted small">
                    {s.last_login_at ? new Date(s.last_login_at).toLocaleDateString() : "never"}
                  </span>
                ) 
              },
              { 
                header: "", 
                render: (s) => (
                  <Button small variant="secondary" onClick={() => setSelected(s)}>
                    Open
                  </Button>
                ) 
              },
            ]} 
          />
          
          <div style={{ padding: "1rem", borderTop: "1px solid var(--c-border)" }}>
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </div>
        </Card>
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
  const [touched, setTouched] = useState<{ [key: string]: boolean }>({});
  const [providerId, setProviderId] = useState(fixedProvider ?? "");
  const [branchId, setBranchId] = useState(fixedBranch ?? "");
  
  const needsProvider = !isGlobalRole(role);
  const needsBranch = role === "BRANCH_ADMIN";
  const branches = useApi<Page<{ branch_id: string; name: string }>>(needsBranch && providerId ? `/api/v1/providers/${providerId}/branches?page_size=100` : null);
  const { busy, error, run } = useAction();

  const errors = {
    full_name: f.full_name.length < 2 ? "Name must be at least 2 characters." : "",
    email: !f.email ? "Email is required." : !isValidEmail(f.email) ? "Please enter a valid email address." : "",
    phone: !isValidPhone(f.phone) ? "Phone must be a 10-digit number." : "",
  };
  
  const handleBlur = (field: string) => setTouched({ ...touched, [field]: true });
  const ready = !errors.full_name && !errors.email && !errors.phone && (!needsProvider || !!providerId) && (!needsBranch || !!branchId);
  
  return (
    <Modal title={`Add ${ROLE_LABEL[role]}`} onClose={onClose}>
      <Alert kind="info">They receive an email to verify their address and choose a password. An email that is already registered is never duplicated.</Alert>
      {error && <Alert kind="error">{error}</Alert>}
      
      <Field label="Full name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} onBlur={() => handleBlur("full_name")} required />
      {touched.full_name && errors.full_name && <div className="field-error">{errors.full_name}</div>}

      <Field label="Email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} onBlur={() => handleBlur("email")} required />
      {touched.email && errors.email && <div className="field-error">{errors.email}</div>}

      <Field label="Phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} onBlur={() => handleBlur("phone")} />
      {touched.phone && errors.phone && <div className="field-error">{errors.phone}</div>}

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
      
      <div className="modal-actions" style={{ marginTop: "1.5rem" }}>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!ready} onClick={() => void run(async () => { 
          await api("/api/v1/staff", { 
            method: "POST", 
            body: { 
              full_name: f.full_name, 
              email: f.email, 
              phone: f.phone || null, 
              assignment: { 
                role, 
                provider_id: needsProvider ? providerId || null : null, 
                branch_id: needsBranch ? branchId || null : null 
              } 
            } 
          }); 
          onDone(); 
        })}>
          Create & send invite
        </Button>
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
  const protectedTarget = targetIsSuperAdmin && me.primary_role !== "SUPER_ADMIN";   
  const canRemove = (a: Assignment) => !self && !protectedTarget && a.status === "ACTIVE" && can("staff.assign_role") && GRANTABLE[me.primary_role].includes(a.role);
  const canChangeStatus = !self && !protectedTarget && isGlobalRole(me.primary_role);      
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
        <div className="stack">
          <div>
            <h3 style={{ fontSize: "1rem", marginBottom: "0.5rem" }}>Changes to this account</h3>
            <AuditTable filters={{ target_type: "staff", target_id: staff.staff_id }} pageSize={5} emptyTitle="No recorded changes" />
          </div>
          <div>
            <h3 style={{ fontSize: "1rem", marginBottom: "0.5rem" }}>Actions by this person</h3>
            <AuditTable filters={{ actor: staff.staff_id }} pageSize={5} emptyTitle="No recorded actions" />
          </div>
        </div>
      ) : (
        <>
          <h3 style={{ fontSize: "1rem", marginBottom: "0.5rem", marginTop: "1rem" }}>Role assignments</h3>
          <DataTable rows={staff.assignments} rowKey={(a) => a.assignment_id} empty={<EmptyState title="No role assignments" />}
            columns={[
              { header: "Role", render: (a) => <span className="badge info">{ROLE_LABEL[a.role]}</span> },
              { header: "Scope", render: scopeLabel },
              { header: "Status", render: (a) => <StatusBadge status={a.status} /> },
              { header: "", render: (a) => canRemove(a) ? <Button small variant="secondary" onClick={() => setConfirm({ kind: "remove", assignment: a })}>Remove</Button> : null },
            ]} />
        </>
      )}
      
      <div className="modal-actions" style={{ marginTop: "1.5rem" }}>
        {canResend && <Button small variant="secondary" busy={act.busy} onClick={() => void act.run(async () => { await api(`/api/v1/staff/${staff.staff_id}/resend-verification`, { method: "POST" }); setNotice("Invitation sent again."); })}>Resend invite</Button>}
        {canChangeStatus && (staff.status === "ACTIVE"
          ? <Button small variant="danger" onClick={() => setConfirm({ kind: "deactivate" })}>Deactivate</Button>
          : <Button small onClick={() => setConfirm({ kind: "reactivate" })}>Reactivate</Button>)}
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