import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, SelectField, Spinner, StatusBadge } from "./ui";

export type Subject = { subject_id: string; code: string; name: string; status: string };
type Batch = { batch_id: string; name: string; status: string };
type Teaching = { teaching_id: string; staff_id: string; subject_id: string; batch_id: string | null };
type Teacher = { staff_id: string; staff_code: string; full_name: string };

/** Which platform subjects a provider offers. Providers choose from the catalogue; they never create subject records, so
 * the same subject is shared (no duplicates) and teachers and batches can only use subjects the provider offers. */
export function ProviderSubjects({ providerId }: { providerId: string }) {
  const { can } = useAuth();
  const catalogue = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  const offered = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const act = useAction();
  const isOffered = (id: string) => offered.data?.some((s) => s.subject_id === id);
  // Subjects the provider already offers stay listed even if the catalogue later deactivated them.
  const rows = [...(catalogue.data?.items ?? []), ...(offered.data ?? []).filter((o) => !catalogue.data?.items.some((c) => c.subject_id === o.subject_id))];
  const manage = can("subject.manage");
  if (catalogue.loading || offered.loading) return <Spinner />;
  return (
    <div className="stack">
      <p className="muted small">The platform catalogue is managed by the Super Admin. {manage ? "Offer the subjects this provider teaches; teachers and batches can then use them." : "Subjects this provider offers are marked."}</p>
      {(catalogue.error || offered.error || act.error) && <Alert kind="error">{catalogue.error || offered.error || act.error}</Alert>}
      <DataTable rows={rows} rowKey={(s) => s.subject_id} empty={<EmptyState title="The catalogue has no subjects yet" hint="A platform administrator adds subjects to the catalogue." />}
        columns={[
          { header: "Code", render: (s) => s.code },
          { header: "Subject", render: (s) => <>{s.name}{s.status === "INACTIVE" && <> <StatusBadge status="INACTIVE" /></>}</> },
          { header: "Offered by this provider", render: (s) => isOffered(s.subject_id) ? <StatusBadge status="ACTIVE" />
            : manage && s.status === "ACTIVE" ? <Button small variant="secondary" busy={act.busy} onClick={() => void act.run(async () => { await api(`/api/v1/providers/${providerId}/subjects/${s.subject_id}`, { method: "PUT" }); offered.reload(); })}>Offer subject</Button>
            : <span className="muted">Not offered</span> },
        ]} />
    </div>
  );
}

/** Teacher-subject assignments in one branch: which teacher teaches which offered subject, to the whole branch or one batch. */
export function TeachingAssignments({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const list = useApi<Page<Teaching>>(`/api/v1/teaching?branch_id=${branchId}&page_size=100`);
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const teachers = useApi<Page<Teacher>>(`/api/v1/staff?role=TEACHER&provider_id=${providerId}&status=ACTIVE&page_size=100`);
  const batches = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const [f, setF] = useState({ staff_id: "", subject_id: "", batch_id: "" });
  const [ending, setEnding] = useState<Teaching | null>(null);
  const act = useAction();
  const manage = can("teaching.assign");
  const sName = (id: string) => subjects.data?.find((s) => s.subject_id === id)?.name ?? "Subject";
  const tName = (id: string) => { const t = teachers.data?.items.find((s) => s.staff_id === id); return t ? `${t.full_name} (${t.staff_code})` : "Teacher"; };
  const bName = (id: string | null) => (id ? batches.data?.items.find((b) => b.batch_id === id)?.name ?? "Batch" : "All batches");
  return (
    <div className="stack">
      {manage && (
        <Card>
          <h3>Assign a teacher</h3>
          {act.error && !ending && <Alert kind="error">{act.error}</Alert>}
          {(subjects.data ?? []).length === 0 && !subjects.loading && <Alert kind="info">This provider offers no subjects yet. Offer subjects first.</Alert>}
          <div className="grid cols-3">
            <SelectField label="Teacher" value={f.staff_id} onChange={(e) => setF({ ...f, staff_id: e.target.value })}>
              <option value="">Select…</option>{(teachers.data?.items ?? []).map((t) => <option key={t.staff_id} value={t.staff_id}>{t.full_name} ({t.staff_code})</option>)}
            </SelectField>
            <SelectField label="Subject" value={f.subject_id} onChange={(e) => setF({ ...f, subject_id: e.target.value })}>
              <option value="">Select…</option>{(subjects.data ?? []).filter((s) => s.status === "ACTIVE").map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
            </SelectField>
            <SelectField label="Batch" value={f.batch_id} onChange={(e) => setF({ ...f, batch_id: e.target.value })}>
              <option value="">All batches in this branch</option>{(batches.data?.items ?? []).filter((b) => b.status === "ACTIVE").map((b) => <option key={b.batch_id} value={b.batch_id}>{b.name}</option>)}
            </SelectField>
          </div>
          <div className="row">
            <Button busy={act.busy} disabled={!f.staff_id || !f.subject_id} onClick={() => void act.run(async () => {
              await api("/api/v1/teaching", { method: "POST", body: { staff_id: f.staff_id, branch_id: branchId, subject_id: f.subject_id, batch_id: f.batch_id || null } });
              setF({ staff_id: "", subject_id: "", batch_id: "" });
              list.reload();
            })}>Assign</Button>
          </div>
        </Card>
      )}
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <DataTable rows={list.data?.items ?? []} rowKey={(t) => t.teaching_id} empty={<EmptyState title="No teaching assignments in this branch" />}
          columns={[
            { header: "Teacher", render: (t) => tName(t.staff_id) },
            { header: "Subject", render: (t) => sName(t.subject_id) },
            { header: "Batch", render: (t) => bName(t.batch_id) },
            { header: "Actions", render: (t) => manage ? <Button small variant="secondary" onClick={() => setEnding(t)}>End</Button> : null },
          ]} />
      )}
      {ending && (
        <ConfirmDialog title="End teaching assignment" danger confirmLabel="End assignment" busy={act.busy}
          message={`${tName(ending.staff_id)} stops teaching ${sName(ending.subject_id)} (${bName(ending.batch_id)}). Papers and results they created are kept.`}
          onCancel={() => setEnding(null)}
          onConfirm={() => void act.run(async () => { await api(`/api/v1/teaching/${ending.teaching_id}`, { method: "DELETE" }); setEnding(null); list.reload(); })}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
        </ConfirmDialog>
      )}
    </div>
  );
}
