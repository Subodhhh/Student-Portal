import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope, type Branch, type Provider } from "../auth/ScopeContext";
import { ProviderSubjects, TeachingAssignments } from "../components/academics";
import { AuditTable } from "../components/audit";
import { StudentDirectory } from "../components/students";
import { StaffList } from "../components/staff";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SearchField, SelectField, Spinner, StatCard, StatusBadge, Tabs, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { Batches } from "./BatchesPage";

type ProviderRow = Provider & { phone?: string | null; address?: string | null; created_at?: string; branch_count?: number; staff_count?: number; student_count?: number };
const day = (s?: string) => (s ? new Date(s).toLocaleDateString() : "—");

export function ProvidersPage() {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);
  const scope = useScope();
  const list = useApi<Page<ProviderRow>>(`/api/v1/providers?page=${page}&page_size=20${q ? `&q=${encodeURIComponent(q)}` : ""}${status ? `&status=${status}` : ""}`);
  return (
    <>
      <PageHeader subtitle="Coaching institutes on the platform, their branches and the staff who work there." />
      <div className="stack">
        <div className="toolbar">
          <SearchField label="Search by name" value={q} onChange={(v) => { setQ(v); setPage(1); }} />
          <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All</option><option value="ACTIVE">Active</option><option value="INACTIVE">Inactive</option>
          </SelectField>
          {can("provider.create") && <Button onClick={() => setCreating(true)}>New provider</Button>}
        </div>
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading && !list.data ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(p) => p.provider_id}
              empty={<EmptyState title={q || status ? "No providers match these filters" : "No providers yet"} />}
              columns={[
                { header: "Name", render: (p) => <><Link to={`/providers/${p.provider_id}`}>{p.name}</Link><div className="muted small">ID {p.provider_id.slice(0, 8)}</div></> },
                { header: "Email", render: (p) => p.email },
                { header: "Branches", render: (p) => p.branch_count ?? "—" },
                { header: "Staff", render: (p) => p.staff_count ?? "—" },
                { header: "Students", render: (p) => p.student_count ?? "—" },
                { header: "Created", render: (p) => day(p.created_at) },
                { header: "Status", render: (p) => <StatusBadge status={p.status} /> },
                { header: "Actions", render: (p) => <Link className="btn secondary small" to={`/providers/${p.provider_id}`} aria-label={`Open ${p.name}`}>Open</Link> },
              ]} />
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </div>
      {creating && <CreateProvider onClose={() => setCreating(false)} onDone={() => { setCreating(false); list.reload(); scope.reload(); }} />}
    </>
  );
}

function CreateProvider({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: "", email: "", phone: "", address: "" });
  const { busy, error, run } = useAction();
  return (
    <Modal title="New provider" onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
      <Field label="Contact email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required />
      <Field label="Phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
      <TextAreaField label="Address" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={f.name.length < 2 || !f.email} onClick={() => void run(async () => { await api("/api/v1/providers", { method: "POST", body: { name: f.name, email: f.email, phone: f.phone || null, address: f.address || null } }); onDone(); })}>Create</Button>
      </div>
    </Modal>
  );
}

type StatusChange = { kind: "provider" | "branch"; id: string; name: string; active: boolean };

function StatusConfirm({ change, onClose, onDone }: { change: StatusChange; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const act = useAction();
  const verb = change.active ? "Deactivate" : "Activate";
  const path = change.kind === "provider"
    ? `/api/v1/providers/${change.id}/${change.active ? "deactivate" : "activate"}`
    : `/api/v1/branches/${change.id}/${change.active ? "deactivate" : "activate"}`;
  return (
    <ConfirmDialog title={`${verb} ${change.name}`} confirmLabel={verb} danger={change.active} busy={act.busy} onCancel={onClose}
      message={change.active ? `The ${change.kind} stops operating; its data is kept and it can be activated again. Recorded in the audit log with your reason.` : `The ${change.kind} operates again. Recorded in the audit log with your reason.`}
      onConfirm={() => void act.run(async () => { await api(path, { method: "POST", body: { reason } }); onDone(); })}>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
    </ConfirmDialog>
  );
}

const useTab = (fallback: string) => {
  const [params, setParams] = useSearchParams();
  return [params.get("tab") ?? fallback, (id: string) => setParams({ tab: id }, { replace: true })] as const;
};

export function ProviderDetailPage() {
  const { providerId = "" } = useParams();
  const { can } = useAuth();
  const scope = useScope();
  const prov = useApi<ProviderRow>(`/api/v1/providers/${providerId}`);
  const [tab, setTab] = useTab("overview");
  const [change, setChange] = useState<StatusChange | null>(null);
  if (prov.loading && !prov.data) return <Spinner />;
  if (prov.error || !prov.data) return <Alert kind="error">{prov.error || "Provider not found."}</Alert>;
  const p = prov.data;
  return (
    <>
      <PageHeader crumbs={[<Link key="list" to="/providers">Providers & Branches</Link>, p.name]} title={p.name}
        subtitle={[p.email, p.phone].filter(Boolean).join(" · ")}
        actions={<>
          <StatusBadge status={p.status} />
          {can("provider.deactivate") && <Button variant={p.status === "ACTIVE" ? "danger" : "secondary"} small onClick={() => setChange({ kind: "provider", id: p.provider_id, name: p.name, active: p.status === "ACTIVE" })}>{p.status === "ACTIVE" ? "Deactivate" : "Activate"}</Button>}
        </>} />
      <Tabs active={tab} onChange={setTab} tabs={[
        { id: "overview", label: "Overview" },
        { id: "branches", label: "Branches" },
        ...(can("staff.read") ? [{ id: "staff", label: "Staff" }] : []),
        ...(can("enrollment.manage") ? [{ id: "students", label: "Students" }] : []),
        { id: "subjects", label: "Offered subjects" },
        ...(can("audit.read") ? [{ id: "activity", label: "Activity" }] : []),
      ]} />
      {tab === "overview" && (
        <div className="stack">
          <div className="grid stats">
            <StatCard label="Branches" value={p.branch_count ?? 0} />
            <StatCard label="Staff with an active role" value={p.staff_count ?? 0} />
            <StatCard label="Enrolled students" value={p.student_count ?? 0} />
          </div>
          <Card>
            <h2>Contact</h2>
            <dl className="facts">
              <dt>Email</dt><dd>{p.email}</dd>
              <dt>Phone</dt><dd>{p.phone || "—"}</dd>
              <dt>Address</dt><dd>{p.address || "—"}</dd>
              <dt>On the platform since</dt><dd>{day(p.created_at)}</dd>
              <dt>Provider ID</dt><dd><code>{p.provider_id}</code></dd>
            </dl>
          </Card>
        </div>
      )}
      {tab === "branches" && <ProviderBranches providerId={providerId} onChange={setChange} />}
      {tab === "students" && can("enrollment.manage") && <StudentDirectory key={providerId} providerId={providerId} />}
      {tab === "activity" && can("audit.read") && <AuditTable key={providerId} filters={{ provider_id: providerId }} showProvider={false} emptyTitle="No recorded activity for this provider" />}
      {tab === "staff" && can("staff.read") && (
        <>
          <p className="muted small">Provider Admins, Branch Admins and Teachers with an active role at {p.name}.</p>
          <StaffList key={providerId} providerId={providerId} roleFilter />
        </>
      )}
      {tab === "subjects" && <ProviderSubjects providerId={providerId} />}
      {change && <StatusConfirm change={change} onClose={() => setChange(null)} onDone={() => { setChange(null); prov.reload(); scope.reload(); }} />}
    </>
  );
}

function ProviderBranches({ providerId, onChange }: { providerId: string; onChange: (c: StatusChange) => void }) {
  const { can } = useAuth();
  const scope = useScope();
  const branches = useApi<Page<Branch>>(`/api/v1/providers/${providerId}/branches?page_size=100`);
  const [adding, setAdding] = useState(false);
  return (
    <div className="stack">
      {can("branch.create") && <div className="toolbar end"><Button onClick={() => setAdding(true)}>Add branch</Button></div>}
      {branches.error && <Alert kind="error">{branches.error}</Alert>}
      {branches.loading && !branches.data ? <Spinner /> : (
        <DataTable rows={branches.data?.items ?? []} rowKey={(b) => b.branch_id} empty={<EmptyState title="No branches" hint="Add the first branch to start enrolling students." />}
          columns={[
            { header: "Branch", render: (b) => <Link to={`/providers/${providerId}/branches/${b.branch_id}`}>{b.name}</Link> },
            { header: "City", render: (b) => b.city ?? "—" },
            { header: "Status", render: (b) => <StatusBadge status={b.status} /> },
            { header: "Actions", render: (b) => (
              <div className="row">
                <Link className="btn secondary small" to={`/providers/${providerId}/branches/${b.branch_id}`} aria-label={`Open ${b.name}`}>Open</Link>
                {can("branch.deactivate") && <Button small variant={b.status === "ACTIVE" ? "secondary" : "primary"} onClick={() => onChange({ kind: "branch", id: b.branch_id, name: b.name, active: b.status === "ACTIVE" })}>{b.status === "ACTIVE" ? "Deactivate" : "Activate"}</Button>}
              </div>
            ) },
          ]} />
      )}
      {adding && <AddBranch providerId={providerId} onClose={() => setAdding(false)} onDone={() => { setAdding(false); branches.reload(); scope.reload(); }} />}
    </div>
  );
}

function AddBranch({ providerId, onClose, onDone }: { providerId: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: "", city: "", state: "", pincode: "", address: "" });
  const { busy, error, run } = useAction();
  return (
    <Modal title="Add branch" onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Branch name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
      <div className="grid cols-2">
        <Field label="City" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} />
        <Field label="State" value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })} />
      </div>
      <Field label="PIN code" value={f.pincode} onChange={(e) => setF({ ...f, pincode: e.target.value })} maxLength={10} />
      <TextAreaField label="Address" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={f.name.length < 2} onClick={() => void run(async () => {
          await api(`/api/v1/providers/${providerId}/branches`, { method: "POST", body: { name: f.name, city: f.city || null, state: f.state || null, pincode: f.pincode || null, address: f.address || null } });
          onDone();
        })}>Add branch</Button>
      </div>
    </Modal>
  );
}

/** One branch: the staff who work there (its Branch Admins and the Teachers teaching in it) and its teaching assignments. */
export function BranchDetailPage() {
  const { providerId = "", branchId = "" } = useParams();
  const { can } = useAuth();
  const branch = useApi<Branch & { state?: string | null }>(`/api/v1/branches/${branchId}`);
  const prov = useApi<Provider>(`/api/v1/providers/${providerId}`);
  const [tab, setTab] = useTab(can("staff.read") ? "staff" : "teaching");
  if ((branch.loading && !branch.data) || (prov.loading && !prov.data)) return <Spinner />;
  if (branch.error || !branch.data || branch.data.provider_id !== providerId) return <Alert kind="error">{branch.error || "Branch not found."}</Alert>;
  const b = branch.data;
  const providerName = prov.data?.name ?? "Provider";
  return (
    <>
      <PageHeader crumbs={[<Link key="list" to="/providers">Providers & Branches</Link>, <Link key="p" to={`/providers/${providerId}`}>{providerName}</Link>, b.name]}
        title={b.name} subtitle={[providerName, b.city, b.state].filter(Boolean).join(" · ")} actions={<StatusBadge status={b.status} />} />
      <Tabs active={tab} onChange={setTab} tabs={[
        ...(can("staff.read") ? [{ id: "staff", label: "Staff" }] : []),
        ...(can("enrollment.manage") ? [{ id: "students", label: "Students" }] : []),
        ...(can("batch.manage") ? [{ id: "batches", label: "Batches" }] : []),
        { id: "teaching", label: "Teaching assignments" },
      ]} />
      {tab === "students" && can("enrollment.manage") && <StudentDirectory key={branchId} providerId={providerId} branchId={branchId} />}
      {tab === "staff" && can("staff.read") && (
        <>
          <p className="muted small">Branch Admins of {b.name} and Teachers teaching in it.</p>
          <StaffList key={branchId} branchId={branchId} roleFilter />
        </>
      )}
      {tab === "batches" && can("batch.manage") && <Batches key={branchId} providerId={providerId} branchId={branchId} />}
      {tab === "teaching" && <TeachingAssignments providerId={providerId} branchId={branchId} />}
    </>
  );
}
