import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import type { Subject } from "../components/academics";
import { AcademicFields } from "../components/classLevels";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, DataTable, EmptyState, Field, Modal, PageHeader, Spinner, StatusBadge } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { type AcademicForm, EMPTY_ACADEMIC, academicErrors, classLabel } from "../services/classLevels";

// default_*: optional Class / Academic Year suggested when enrolling students into the batch (prefill only).
type Batch = { batch_id: string; name: string; status: string; start_date: string | null; end_date: string | null;
  default_class_level?: string | null; default_academic_year?: string | null; default_other_description?: string | null };

const defaultsText = (b: Batch) => [b.default_class_level ? classLabel(b.default_class_level) + (b.default_other_description ? ` (${b.default_other_description})` : "") : null,
  b.default_academic_year].filter(Boolean).join(" · ");
const defaultsBody = (f: AcademicForm) => ({ default_class_level: f.class_level || null, default_academic_year: f.academic_year.trim() || null,
  default_other_description: f.class_level === "OTHER" ? f.other_class_description.trim() : null });

/** Batches: a branch's student groups. Students join batches (Students & Enrollments), and each batch takes a set of the
 * provider's offered subjects, which is how students are assigned to subjects and become eligible for exams. */
export default function BatchesPage() {
  return <NeedBranch>{({ providerId, branchId }) => <Batches providerId={providerId} branchId={branchId} />}</NeedBranch>;
}

/** A branch's batches (also the Batches tab of a branch's page): everything is addressed by this branch's ID. */
export function Batches({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const list = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const [open, setOpen] = useState(false);
  const [mapFor, setMapFor] = useState<Batch | null>(null);
  const [defaultsFor, setDefaultsFor] = useState<Batch | null>(null);
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
            { header: "Class defaults", render: (b) => defaultsText(b) || "—" },
            { header: "Actions", render: (b) => manage ? (
              <div className="row">
                <Button small variant="secondary" onClick={() => setMapFor(b)}>Subjects</Button>
                <Button small variant="secondary" onClick={() => setDefaultsFor(b)}>Class defaults</Button>
              </div>
            ) : null },
          ]} />
      )}
      {open && <NewBatch branchId={branchId} onClose={() => setOpen(false)} onDone={() => { setOpen(false); list.reload(); }} />}
      {mapFor && <BatchSubjects batch={mapFor} providerId={providerId} onClose={() => setMapFor(null)} />}
      {defaultsFor && <BatchDefaults batch={defaultsFor} onClose={() => setDefaultsFor(null)} onDone={() => { setDefaultsFor(null); list.reload(); }} />}
    </>
  );
}

function NewBatch({ branchId, onClose, onDone }: { branchId: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: "", start_date: "", end_date: "" });
  const [defaults, setDefaults] = useState<AcademicForm>(EMPTY_ACADEMIC);
  const [showErrors, setShowErrors] = useState(false);
  const { busy, error, run } = useAction();
  const create = () => void run(async () => {
    if (Object.keys(academicErrors(defaults, false)).length) { setShowErrors(true); return; }
    await api(`/api/v1/branches/${branchId}/batches`, { method: "POST", body: { name: f.name, start_date: f.start_date || null, end_date: f.end_date || null, ...defaultsBody(defaults) } });
    onDone();
  });
  return (
    <Modal title="New batch" onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <div className="grid cols-2"><Field label="Start date" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /><Field label="End date" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></div>
      <h3>Class defaults (optional)</h3>
      <p className="muted small">Prefill the class and academic year when students are enrolled into this batch. Staff can still change them per student.</p>
      <AcademicFields value={defaults} onChange={setDefaults} required={false} showErrors={showErrors} labels={{ cls: "Default class", year: "Default academic year" }} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={f.name.length < 2} onClick={create}>Create</Button>
      </div>
    </Modal>
  );
}

/** Edit a batch's optional Class / Academic Year defaults. Existing enrollments keep their confirmed Class: defaults only prefill. */
function BatchDefaults({ batch, onClose, onDone }: { batch: Batch; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<AcademicForm>({ class_level: batch.default_class_level ?? "", academic_year: batch.default_academic_year ?? "",
    other_class_description: batch.default_other_description ?? "" });
  const [showErrors, setShowErrors] = useState(false);
  const { busy, error, run } = useAction();
  const save = () => void run(async () => {
    if (Object.keys(academicErrors(f, false)).length) { setShowErrors(true); return; }
    await api(`/api/v1/batches/${batch.batch_id}`, { method: "PATCH", body: defaultsBody(f) });
    onDone();
  });
  return (
    <Modal title={`Class defaults: ${batch.name}`} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <p className="muted small">Used to prefill new enrollments into this batch. Changing them does not change students already enrolled.</p>
      <AcademicFields value={f} onChange={setF} required={false} showErrors={showErrors} labels={{ cls: "Default class", year: "Default academic year" }} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} onClick={save}>Save defaults</Button>
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
