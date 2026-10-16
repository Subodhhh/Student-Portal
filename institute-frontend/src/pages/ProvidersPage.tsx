import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope, type Branch, type Provider } from "../auth/ScopeContext";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

export function ProvidersPage() {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const scope = useScope();
  const list = useApi<Page<Provider>>(`/api/v1/providers?page=${page}&page_size=20${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  return (
    <>
      <PageHeader title="Providers" subtitle="Coaching institutes on the platform" actions={can("provider.create") && <Button onClick={() => setCreating(true)}>New provider</Button>} />
      <div className="stack">
        <Field label="Search by name" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(p) => p.provider_id}
              empty={<EmptyState title="No providers found" />}
              columns={[
                { header: "Name", render: (p) => <Link to={`/providers/${p.provider_id}`}>{p.name}</Link> },
                { header: "Email", render: (p) => p.email },
                { header: "Status", render: (p) => <StatusBadge status={p.status} /> },
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
      <Button busy={busy} disabled={f.name.length < 2 || !f.email} onClick={() => void run(async () => { await api("/api/v1/providers", { method: "POST", body: { name: f.name, email: f.email, phone: f.phone || null, address: f.address || null } }); onDone(); })}>Create</Button>
    </Modal>
  );
}

export function ProviderDetailPage() {
  const { providerId = "" } = useParams();
  const { can } = useAuth();
  const scope = useScope();
  const prov = useApi<Provider>(`/api/v1/providers/${providerId}`);
  const branches = useApi<Page<Branch>>(`/api/v1/providers/${providerId}/branches?page_size=100`);
  const [adding, setAdding] = useState(false);
  const [deactivate, setDeactivate] = useState<null | { kind: "provider" | "branch"; id: string; active: boolean }>(null);
  const [reason, setReason] = useState("");
  const act = useAction();
  if (prov.loading) return <Spinner />;
  if (prov.error || !prov.data) return <Alert kind="error">{prov.error || "Not found"}</Alert>;
  const p = prov.data;
  const confirm = () => act.run(async () => {
    if (!deactivate) return;
    const path =
      deactivate.kind === "provider"
        ? `/api/v1/providers/${deactivate.id}/${deactivate.active ? "deactivate" : "activate"}`
        : `/api/v1/branches/${deactivate.id}/${deactivate.active ? "deactivate" : "activate"}`;
    await api(path, { method: "POST", body: { reason } });
    setDeactivate(null); setReason(""); prov.reload(); branches.reload(); scope.reload();
  });
  return (
    <>
      <PageHeader crumbs={["Providers", p.name]} title={p.name} subtitle={p.email} actions={<>
        <StatusBadge status={p.status} />
        {can("provider.deactivate") && <Button variant={p.status === "ACTIVE" ? "danger" : "secondary"} small onClick={() => setDeactivate({ kind: "provider", id: p.provider_id, active: p.status === "ACTIVE" })}>{p.status === "ACTIVE" ? "Deactivate" : "Activate"}</Button>}
      </>} />
      <Card>
        <div className="row between"><h2>Branches</h2>{can("branch.create") && <Button small onClick={() => setAdding(true)}>Add branch</Button>}</div>
        {branches.error && <Alert kind="error">{branches.error}</Alert>}
        <DataTable rows={branches.data?.items ?? []} rowKey={(b) => b.branch_id} empty={<EmptyState title="No branches" hint="Add the first branch to start enrolling students." />}
          columns={[
            { header: "Branch", render: (b) => b.name }, { header: "City", render: (b) => b.city ?? "—" },
            { header: "Status", render: (b) => <StatusBadge status={b.status} /> },
            {
              header: "",
                render: (b) =>
                  can("branch.deactivate") ? (
                    <Button
                      small
                      variant={b.status === "ACTIVE" ? "secondary" : "primary"}
                      onClick={() =>
                        setDeactivate({
                          kind: "branch",
                          id: b.branch_id,
                          active: b.status === "ACTIVE",
                        })
                      }
                    >
                      {b.status === "ACTIVE" ? "Deactivate" : "Reactivate"}
                    </Button>
                  ) : null,
            },
          ]} />
      </Card>
      {adding && <AddBranch providerId={providerId} onClose={() => setAdding(false)} onDone={() => { setAdding(false); branches.reload(); scope.reload(); }} />}
      {deactivate && (
        <ConfirmDialog title={`${deactivate.active ? "Deactivate" : "Activate"} ${deactivate.kind}`} message="This is recorded in the audit log together with your reason."
          confirmLabel="Confirm" danger={deactivate.active} busy={act.busy} onCancel={() => setDeactivate(null)} onConfirm={() => void confirm()}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        </ConfirmDialog>
      )}
    </>
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
      <Button busy={busy} disabled={f.name.length < 2} onClick={() => void run(async () => {
        await api(`/api/v1/providers/${providerId}/branches`, { method: "POST", body: { name: f.name, city: f.city || null, state: f.state || null, pincode: f.pincode || null, address: f.address || null } });
        onDone();
      })}>Add branch</Button>
    </Modal>
  );
}
