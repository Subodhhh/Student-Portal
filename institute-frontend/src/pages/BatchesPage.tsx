import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import type { Subject } from "../components/academics";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, DataTable, EmptyState, Field, Modal, PageHeader, Spinner, StatusBadge } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

type Batch = { batch_id: string; name: string; status: string; start_date: string | null; end_date: string | null };

/** Batches: a branch's student groups. Students join batches (Students & Enrollments), and each batch takes a set of the
 * provider's offered subjects, which is how students are assigned to subjects and become eligible for exams. */
export default function BatchesPage() {
  return <NeedBranch>{({ providerId, branchId }) => <Batches providerId={providerId} branchId={branchId} />}</NeedBranch>;
}

function Batches({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const list = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const [open, setOpen] = useState(false);
  const [mapFor, setMapFor] = useState<Batch | null>(null);
  const manage = can("batch.manage");
  return (
    <>
      <PageHeader subtitle="Student groups of this branch and the subjects each group takes." actions={manage && <Button onClick={() => setOpen(true)}>New batch</Button>} />
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <DataTable rows={list.data?.items ?? []} rowKey={(b) => b.batch_id} empty={<EmptyState title="No batches yet" hint="Create a batch, then add its subjects and enroll students into it." />}
          columns={[
            { header: "Name", render: (b) => b.name },
            { header: "Dates", render: (b) => `${b.start_date ?? "—"} → ${b.end_date ?? "—"}` },
            { header: "Status", render: (b) => <StatusBadge status={b.status} /> },
            { header: "Actions", render: (b) => manage ? <Button small variant="secondary" onClick={() => setMapFor(b)}>Subjects</Button> : null },
          ]} />
      )}
      {open && <NewBatch branchId={branchId} onClose={() => setOpen(false)} onDone={() => { setOpen(false); list.reload(); }} />}
      {mapFor && <BatchSubjects batch={mapFor} providerId={providerId} onClose={() => setMapFor(null)} />}
    </>
  );
}

function NewBatch({ branchId, onClose, onDone }: { branchId: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: "", start_date: "", end_date: "" });
  const { busy, error, run } = useAction();
  return (
    <Modal title="New batch" onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <div className="grid cols-2"><Field label="Start date" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /><Field label="End date" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></div>
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={f.name.length < 2} onClick={() => void run(async () => { await api(`/api/v1/branches/${branchId}/batches`, { method: "POST", body: { name: f.name, start_date: f.start_date || null, end_date: f.end_date || null } }); onDone(); })}>Create</Button>
      </div>
    </Modal>
  );
}

function BatchSubjects({ batch, providerId, onClose }: { batch: Batch; providerId: string; onClose: () => void }) {
  const mapped = useApi<Subject[]>(`/api/v1/batches/${batch.batch_id}/subjects`);
  const offered = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const act = useAction();
  const isMapped = (id: string) => mapped.data?.some((s) => s.subject_id === id);
  return (
    <Modal title={`Subjects in ${batch.name}`} onClose={onClose}>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {(offered.data ?? []).length === 0 && !offered.loading && <EmptyState title="This provider offers no subjects yet" hint="Offer subjects first (Subjects page)." />}
      <fieldset className="checklist">
        <legend className="sr-only">Subjects taken by this batch</legend>
        {(offered.data ?? []).map((s) => (
          <label key={s.subject_id} className="row"><input type="checkbox" checked={!!isMapped(s.subject_id)} disabled={act.busy} onChange={() => void act.run(async () => {
            await api(`/api/v1/batches/${batch.batch_id}/subjects/${s.subject_id}`, { method: isMapped(s.subject_id) ? "DELETE" : "PUT" });
            mapped.reload();
          })} />{s.name}</label>
        ))}
      </fieldset>
      <div className="modal-actions"><Button variant="secondary" onClick={onClose}>Done</Button></div>
    </Modal>
  );
}
