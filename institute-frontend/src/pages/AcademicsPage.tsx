import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, DataTable, EmptyState, Field, Modal, PageHeader, SelectField, Spinner, StatusBadge, Tabs } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

type Subject = { subject_id: string; code: string; name: string };
type Batch = { batch_id: string; name: string; status: string; start_date: string | null; end_date: string | null };
type Teaching = { teaching_id: string; staff_id: string; subject_id: string; batch_id: string | null };
type Staff = { staff_id: string; staff_code: string; full_name: string; assignments: { role: string; status: string }[] };

export default function AcademicsPage() {
  return <NeedBranch>{({ providerId, branchId }) => <Inner providerId={providerId} branchId={branchId} />}</NeedBranch>;
}

function Inner({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const [tab, setTab] = useState("batches");
  return (
    <>
      <PageHeader title="Subjects, batches & teaching" subtitle="Who teaches what, to which batch" />
      <Tabs active={tab} onChange={setTab} tabs={[{ id: "batches", label: "Batches" }, { id: "subjects", label: "Subjects" }, ...(can("teaching.assign") ? [{ id: "teaching", label: "Teaching assignments" }] : [])]} />
      {tab === "batches" && <Batches branchId={branchId} providerId={providerId} />}
      {tab === "subjects" && <Subjects providerId={providerId} />}
      {tab === "teaching" && <TeachingTab providerId={providerId} branchId={branchId} />}
    </>
  );
}

function Batches({ branchId, providerId }: { branchId: string; providerId: string }) {
  const { can } = useAuth();
  const list = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const [open, setOpen] = useState(false);
  const [mapFor, setMapFor] = useState<Batch | null>(null);
  return (
    <Card>
      <div className="row between"><h2>Batches</h2>{can("batch.manage") && <Button small onClick={() => setOpen(true)}>New batch</Button>}</div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading ? <Spinner /> : <DataTable rows={list.data?.items ?? []} rowKey={(b) => b.batch_id} empty={<EmptyState title="No batches yet" />}
        columns={[{ header: "Name", render: (b) => b.name }, { header: "Dates", render: (b) => `${b.start_date ?? "—"} → ${b.end_date ?? "—"}` }, { header: "Status", render: (b) => <StatusBadge status={b.status} /> },
          { header: "", render: (b) => can("batch.manage") && <Button small variant="secondary" onClick={() => setMapFor(b)}>Subjects</Button> }]} />}
      {open && <NewBatch branchId={branchId} onClose={() => setOpen(false)} onDone={() => { setOpen(false); list.reload(); }} />}
      {mapFor && <BatchSubjects batch={mapFor} providerId={providerId} onClose={() => setMapFor(null)} />}
    </Card>
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
      <Button busy={busy} disabled={f.name.length < 2} onClick={() => void run(async () => { await api(`/api/v1/branches/${branchId}/batches`, { method: "POST", body: { name: f.name, start_date: f.start_date || null, end_date: f.end_date || null } }); onDone(); })}>Create</Button>
    </Modal>
  );
}

function BatchSubjects({ batch, providerId, onClose }: { batch: Batch; providerId: string; onClose: () => void }) {
  const mapped = useApi<Subject[]>(`/api/v1/batches/${batch.batch_id}/subjects`);
  const enabled = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const act = useAction();
  const isMapped = (id: string) => mapped.data?.some((s) => s.subject_id === id);
  return (
    <Modal title={`Subjects in ${batch.name}`} onClose={onClose}>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {(enabled.data ?? []).length === 0 && <EmptyState title="No subjects enabled" hint="Enable subjects for the provider first (Subjects tab)." />}
      {(enabled.data ?? []).map((s) => (
        <label key={s.subject_id} className="row"><input type="checkbox" checked={!!isMapped(s.subject_id)} onChange={() => void act.run(async () => {
          await api(`/api/v1/batches/${batch.batch_id}/subjects/${s.subject_id}`, { method: isMapped(s.subject_id) ? "DELETE" : "PUT" });
          mapped.reload();
        })} />{s.name}</label>
      ))}
    </Modal>
  );
}

function Subjects({ providerId }: { providerId: string }) {
  const { can, me } = useAuth();
  const all = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  const enabled = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const [creating, setCreating] = useState(false);
  const act = useAction();
  const global = me?.primary_role === "SUPER_ADMIN" || me?.primary_role === "ADMIN";
  const isOn = (id: string) => enabled.data?.some((s) => s.subject_id === id);
  return (
    <Card>
      <div className="row between"><h2>Subjects</h2>{global && <Button small onClick={() => setCreating(true)}>New subject</Button>}</div>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <DataTable rows={all.data?.items ?? []} rowKey={(s) => s.subject_id} empty={<EmptyState title="No subjects in the catalogue" hint="A platform admin adds subjects to the catalogue." />}
        columns={[{ header: "Code", render: (s) => s.code }, { header: "Name", render: (s) => s.name }, { header: "Enabled for provider", render: (s) => isOn(s.subject_id) ? <StatusBadge status="ACTIVE" /> : (can("subject.manage") ? <Button small variant="secondary" onClick={() => void act.run(async () => { await api(`/api/v1/providers/${providerId}/subjects/${s.subject_id}`, { method: "PUT" }); enabled.reload(); })}>Enable</Button> : "—") }]} />
      {creating && <NewSubject onClose={() => setCreating(false)} onDone={() => { setCreating(false); all.reload(); }} />}
    </Card>
  );
}

function NewSubject({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const { busy, error, run } = useAction();
  return (
    <Modal title="New subject" onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} hint="Capital letters, digits, underscore" />
      <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} />
      <Button busy={busy} disabled={code.length < 2 || name.length < 2} onClick={() => void run(async () => { await api("/api/v1/subjects", { method: "POST", body: { code, name } }); onDone(); })}>Create</Button>
    </Modal>
  );
}

function TeachingTab({ providerId, branchId }: { providerId: string; branchId: string }) {
  const list = useApi<Page<Teaching>>(`/api/v1/teaching?branch_id=${branchId}&page_size=100`);
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const teachers = useApi<Page<Staff>>("/api/v1/staff?role=TEACHER&page_size=100");
  const batches = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const [f, setF] = useState({ staff_id: "", subject_id: "", batch_id: "" });
  const act = useAction();
  const sName = (id: string) => subjects.data?.find((s) => s.subject_id === id)?.name ?? id.slice(0, 8);
  const tName = (id: string) => teachers.data?.items.find((s) => s.staff_id === id)?.full_name ?? id.slice(0, 8);
  const bName = (id: string | null) => (id ? batches.data?.items.find((b) => b.batch_id === id)?.name ?? id.slice(0, 8) : "All batches");
  return (
    <div className="stack">
      <Card>
        <h2>Assign a teacher</h2>
        {act.error && <Alert kind="error">{act.error}</Alert>}
        <div className="grid cols-2">
          <SelectField label="Teacher" value={f.staff_id} onChange={(e) => setF({ ...f, staff_id: e.target.value })}><option value="">Select…</option>{(teachers.data?.items ?? []).map((t) => <option key={t.staff_id} value={t.staff_id}>{t.full_name} ({t.staff_code})</option>)}</SelectField>
          <SelectField label="Subject" value={f.subject_id} onChange={(e) => setF({ ...f, subject_id: e.target.value })}><option value="">Select…</option>{(subjects.data ?? []).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}</SelectField>
          <SelectField label="Batch" value={f.batch_id} onChange={(e) => setF({ ...f, batch_id: e.target.value })}><option value="">All batches in this branch</option>{(batches.data?.items ?? []).map((b) => <option key={b.batch_id} value={b.batch_id}>{b.name}</option>)}</SelectField>
        </div>
        <Button busy={act.busy} disabled={!f.staff_id || !f.subject_id} onClick={() => void act.run(async () => { await api("/api/v1/teaching", { method: "POST", body: { staff_id: f.staff_id, branch_id: branchId, subject_id: f.subject_id, batch_id: f.batch_id || null } }); list.reload(); })}>Assign</Button>
      </Card>
      <Card>
        <h2>Current assignments</h2>
        <DataTable rows={list.data?.items ?? []} rowKey={(t) => t.teaching_id} empty={<EmptyState title="No teaching assignments" />}
          columns={[{ header: "Teacher", render: (t) => tName(t.staff_id) }, { header: "Subject", render: (t) => sName(t.subject_id) }, { header: "Batch", render: (t) => bName(t.batch_id) },
            { header: "", render: (t) => <Button small variant="secondary" onClick={() => void act.run(async () => { await api(`/api/v1/teaching/${t.teaching_id}`, { method: "DELETE" }); list.reload(); })}>End</Button> }]} />
      </Card>
    </div>
  );
}
