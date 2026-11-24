import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import type { Subject } from "../components/academics";
import { AcademicFields } from "../components/classLevels";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SearchField, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi, useReference } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { type AcademicForm, EMPTY_ACADEMIC, academicErrors, classLabel } from "../services/classLevels";

// default_*: optional Class / Academic Year suggested when enrolling students into the batch (prefill only).
type Member = { enrollment_id: string; student_id: string; student_name: string | null };
type AddResult = { added: string[]; already_members: string[]; rejected: string[] };
type Batch = { batch_id: string; name: string; description?: string | null; status: string; start_date: string | null; end_date: string | null; student_count?: number | null;
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
  const [status, setStatus] = useState<"ACTIVE" | "INACTIVE">("ACTIVE");
  const list = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?status=${status}&page_size=100`, true);
  const [open, setOpen] = useState(false);
  const [viewing, setViewing] = useState<Batch | null>(null);
  const [editing, setEditing] = useState<Batch | null>(null);
  const [mapFor, setMapFor] = useState<Batch | null>(null);
  const [defaultsFor, setDefaultsFor] = useState<Batch | null>(null);
  const [addTo, setAddTo] = useState<Batch | null>(null);
  const [deleting, setDeleting] = useState<Batch | null>(null);
  const act = useAction();
  const manage = can("batch.manage");
  const enroll = can("enrollment.manage");
  const setBatchStatus = (b: Batch, next: "ACTIVE" | "INACTIVE") => void act.run(async () => {
    await api(`/api/v1/batches/${b.batch_id}`, { method: "PATCH", body: { status: next } });
    setDeleting(null);
    list.reload();
  });
  return (
    <>
      <PageHeader subtitle="Student groups of this branch and the subjects each group takes." actions={manage && <Button onClick={() => setOpen(true)}>New batch</Button>} />
      <div className="toolbar">
        <SelectField label="Show" value={status} onChange={(e) => setStatus(e.target.value as "ACTIVE" | "INACTIVE")}>
          <option value="ACTIVE">Active batches</option><option value="INACTIVE">Inactive batches</option>
        </SelectField>
      </div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {act.error && !deleting && <Alert kind="error">{act.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <DataTable rows={list.data?.items ?? []} rowKey={(b) => b.batch_id}
          empty={status === "ACTIVE" ? <EmptyState title="No batches yet" hint="Create a batch, then add its subjects and enroll students into it." /> : <EmptyState title="No inactive batches" hint="Deleted batches appear here and can be reactivated." />}
          columns={[
            { header: "Name", render: (b) => b.name },
            { header: "Dates", render: (b) => `${b.start_date ?? "—"} → ${b.end_date ?? "—"}` },
            { header: "Students", render: (b) => b.student_count ?? "—" },
            { header: "Status", render: (b) => <StatusBadge status={b.status} /> },
            { header: "Class defaults", render: (b) => defaultsText(b) || "—" },
            { header: "Actions", render: (b) => (
              <div className="row">
                <Button small variant="secondary" onClick={() => setViewing(b)}>View</Button>
                {manage && b.status === "ACTIVE" && <>
                  <Button small variant="secondary" onClick={() => setEditing(b)}>Edit</Button>
                  <Button small variant="secondary" onClick={() => setMapFor(b)}>Subjects</Button>
                  <Button small variant="secondary" onClick={() => setDefaultsFor(b)}>Class defaults</Button>
                </>}
                {enroll && b.status === "ACTIVE" && <Button small variant="secondary" onClick={() => setAddTo(b)}>Add students</Button>}
                {manage && (b.status === "ACTIVE"
                  ? <Button small variant="danger" onClick={() => setDeleting(b)}>Delete</Button>
                  : <Button small busy={act.busy} onClick={() => setBatchStatus(b, "ACTIVE")}>Reactivate</Button>)}
              </div>
            ) },
          ]} />
      )}
      {open && <NewBatch branchId={branchId} onClose={() => setOpen(false)} onDone={() => { setOpen(false); list.reload(); }} />}
      {viewing && <BatchDetails batch={viewing} branchId={branchId} canRemove={enroll} onClose={() => setViewing(null)} onChanged={list.reload} />}
      {editing && <EditBatch batch={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); list.reload(); }} />}
      {mapFor && <BatchSubjects batch={mapFor} providerId={providerId} onClose={() => setMapFor(null)} />}
      {defaultsFor && <BatchDefaults batch={defaultsFor} onClose={() => setDefaultsFor(null)} onDone={() => { setDefaultsFor(null); list.reload(); }} />}
      {addTo && <AddStudents batch={addTo} branchId={branchId} onClose={() => setAddTo(null)} onChanged={list.reload} />}
      {deleting && (
        <ConfirmDialog danger title={`Delete ${deleting.name}?`} confirmLabel="Delete batch" busy={act.busy} onConfirm={() => setBatchStatus(deleting, "INACTIVE")} onCancel={() => setDeleting(null)}
          message="The batch becomes inactive and leaves the normal lists. Its students, teachers, past exams and results are kept, and you can reactivate it from Inactive batches. Until then it gets no new students or exams, including tests already scheduled for it.">
          {act.error && <Alert kind="error">{act.error}</Alert>}
        </ConfirmDialog>
      )}
    </>
  );
}

/** A batch's details, subjects and current students. */
function BatchDetails({ batch, branchId, canRemove, onClose, onChanged }: { batch: Batch; branchId: string; canRemove: boolean; onClose: () => void; onChanged: () => void }) {
  const [page, setPage] = useState(1);
  const subjects = useApi<Subject[]>(`/api/v1/batches/${batch.batch_id}/subjects`);
  const members = useApi<Page<Member>>(canRemove ? `/api/v1/branches/${branchId}/enrollments?batch_id=${batch.batch_id}&status=ACTIVE&page=${page}&page_size=10` : null);
  const act = useAction();
  const remove = (m: Member) => void act.run(async () => {
    await api(`/api/v1/enrollments/${m.enrollment_id}/batches/${batch.batch_id}`, { method: "DELETE" });
    members.reload();
    onChanged();
  });
  return (
    <Modal title={batch.name} onClose={onClose}>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <dl className="small">
        <dt>Status</dt><dd><StatusBadge status={batch.status} /></dd>
        <dt>Dates</dt><dd>{batch.start_date ?? "—"} → {batch.end_date ?? "—"}</dd>
        <dt>Description</dt><dd>{batch.description || "—"}</dd>
        <dt>Class defaults</dt><dd>{defaultsText(batch) || "—"}</dd>
        <dt>Subjects</dt><dd>{subjects.loading ? "Loading…" : (subjects.data ?? []).map((s) => s.name).join(", ") || "—"}</dd>
      </dl>
      {canRemove && <>
        <h3>Students ({members.data?.total ?? batch.student_count ?? 0})</h3>
        {members.error && <Alert kind="error">{members.error}</Alert>}
        {members.loading && !members.data ? <Spinner /> : (
          <>
            <DataTable rows={members.data?.items ?? []} rowKey={(m) => m.enrollment_id} empty={<EmptyState title="No students in this batch" />}
              columns={[
                { header: "Student", render: (m) => m.student_name ?? "—" },
                { header: "Student ID", render: (m) => m.student_id },
                { header: "Actions", render: (m) => <Button small variant="secondary" busy={act.busy} onClick={() => remove(m)}>Remove</Button> },
              ]} />
            <Pagination page={page} pageSize={10} total={members.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </>}
      <div className="modal-actions"><Button variant="secondary" onClick={onClose}>Close</Button></div>
    </Modal>
  );
}

function EditBatch({ batch, onClose, onDone }: { batch: Batch; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: batch.name, description: batch.description ?? "", start_date: batch.start_date ?? "", end_date: batch.end_date ?? "" });
  const { busy, error, run } = useAction();
  const save = () => void run(async () => {
    await api(`/api/v1/batches/${batch.batch_id}`, { method: "PATCH", body: { name: f.name, description: f.description.trim() || null, start_date: f.start_date || null, end_date: f.end_date || null } });
    onDone();
  });
  return (
    <Modal title={`Edit ${batch.name}`} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <TextAreaField label="Description" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="grid cols-2"><Field label="Start date" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /><Field label="End date" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></div>
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={f.name.trim().length < 2} onClick={save}>Save</Button>
      </div>
    </Modal>
  );
}

/** Pick students already enrolled in this branch who are not in the batch yet. Creates memberships only. */
function AddStudents({ batch, branchId, onClose, onChanged }: { batch: Batch; branchId: string; onClose: () => void; onChanged: () => void }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<AddResult | null>(null);
  const eligible = useApi<Page<Member>>(`/api/v1/branches/${branchId}/enrollments?not_in_batch=${batch.batch_id}&status=ACTIVE&page=${page}&page_size=10${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`);
  const { busy, error, run } = useAction();
  const toggle = (id: string) => setPicked((cur) => { const next = new Set(cur); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const add = () => void run(async () => {
    setDone(await api<AddResult>(`/api/v1/batches/${batch.batch_id}/members`, { method: "POST", body: { enrollment_ids: [...picked] } }));
    setPicked(new Set());
    setPage(1);
    eligible.reload();
    onChanged();
  });
  return (
    <Modal title={`Add students to ${batch.name}`} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      {eligible.error && <Alert kind="error">{eligible.error}</Alert>}
      {done && <Alert kind={done.rejected.length ? "warn" : "success"}>Added {done.added.length}.{done.already_members.length > 0 && ` ${done.already_members.length} already in this batch.`}{done.rejected.length > 0 && ` ${done.rejected.length} could not be added (no longer an active enrollment of this branch).`}</Alert>}
      <SearchField label="Search by Student ID" value={q} onChange={(v) => { setQ(v); setPage(1); }} />
      {eligible.loading && !eligible.data ? <Spinner /> : (
        <>
          <DataTable rows={eligible.data?.items ?? []} rowKey={(m) => m.enrollment_id}
            empty={<EmptyState title="No students to add" hint="Every active student of this branch is already in this batch, or none match. Enroll new students on the Students page." />}
            columns={[
              { header: "Select", render: (m) => <input type="checkbox" aria-label={`Select ${m.student_id}`} checked={picked.has(m.enrollment_id)} onChange={() => toggle(m.enrollment_id)} /> },
              { header: "Student", render: (m) => m.student_name ?? "—" },
              { header: "Student ID", render: (m) => m.student_id },
            ]} />
          <Pagination page={page} pageSize={10} total={eligible.data?.total ?? 0} onChange={setPage} />
        </>
      )}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Close</Button>
        <Button busy={busy} disabled={picked.size === 0 || picked.size > 100} onClick={add}>Add {picked.size || ""} {picked.size === 1 ? "student" : "students"}</Button>
      </div>
    </Modal>
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
  const offered = useReference<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
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
