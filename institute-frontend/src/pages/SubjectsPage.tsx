import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { ProviderSubjects, TeachingAssignments, type Subject } from "../components/academics";
import NeedBranch from "../components/NeedBranch";
import ScopeBar from "../components/ScopeBar";
import { isGlobalRole } from "../auth/roles";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SearchField, SelectField, Spinner, StatusBadge, Tabs, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

/** Subjects. Global roles manage the platform catalogue; provider and branch staff choose which catalogue subjects their
 * provider offers and who teaches them. Batches (student groups) are a separate page. */
export default function SubjectsPage() {
  const { me } = useAuth();
  return isGlobalRole(me?.primary_role) ? <SubjectCatalogue /> : <ProviderSubjectsPage />;
}

// ------------------------------------------------------------------ platform catalogue (Super Admin / Admin)

function SubjectCatalogue() {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("ACTIVE");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Subject | null>(null);
  const [toggling, setToggling] = useState<Subject | null>(null);
  const [notice, setNotice] = useState("");
  const list = useApi<Page<Subject>>(`/api/v1/subjects?page=${page}&page_size=20&status=${status}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const manage = can("subject.manage");
  const done = (msg: string) => { setNotice(msg); list.reload(); };
  return (
    <>
      <PageHeader subtitle="The subjects offered on the platform. Providers choose from this catalogue for their teachers and students." />
      <div className="stack">
        <div className="toolbar">
          <SearchField label="Search by name or code" value={q} onChange={(v) => { setQ(v); setPage(1); }} />
          <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="ACTIVE">Active</option><option value="INACTIVE">Inactive</option><option value="ALL">All</option>
          </SelectField>
          {manage && <Button onClick={() => { setNotice(""); setAdding(true); }}>Add subject</Button>}
        </div>
        {notice && <Alert kind="success">{notice}</Alert>}
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading && !list.data ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(s) => s.subject_id}
              empty={<EmptyState title={q ? "No subjects match your search" : status === "INACTIVE" ? "No inactive subjects" : "No subjects yet"}
                hint={!q && status !== "INACTIVE" ? "Add the first subject providers can offer." : undefined}
                action={!q && status !== "INACTIVE" && manage ? <Button onClick={() => setAdding(true)}>Add subject</Button> : undefined} />}
              columns={[
                { header: "Code", render: (s) => <code>{s.code}</code> },
                { header: "Name", render: (s) => s.name },
                { header: "Status", render: (s) => <StatusBadge status={s.status} /> },
                { header: "Actions", render: (s) => manage ? (
                  <div className="row">
                    <Button small variant="secondary" onClick={() => { setNotice(""); setEditing(s); }} aria-label={`Edit ${s.name}`}>Edit</Button>
                    <Button small variant={s.status === "ACTIVE" ? "danger" : "secondary"} onClick={() => { setNotice(""); setToggling(s); }}
                      aria-label={`${s.status === "ACTIVE" ? "Deactivate" : "Reactivate"} ${s.name}`}>{s.status === "ACTIVE" ? "Deactivate" : "Reactivate"}</Button>
                  </div>
                ) : null },
              ]} />
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </div>
      {adding && <SubjectForm onClose={() => setAdding(false)} onDone={(s) => { setAdding(false); done(`${s.name} was added to the catalogue.`); }} />}
      {editing && <SubjectForm subject={editing} onClose={() => setEditing(null)} onDone={(s) => { setEditing(null); done(`${s.name} was saved.`); }} />}
      {toggling && <ToggleSubject subject={toggling} onClose={() => setToggling(null)} onDone={(s) => { setToggling(null); done(`${s.name} is now ${s.status === "ACTIVE" ? "active" : "inactive"}.`); }} />}
    </>
  );
}

function SubjectForm({ subject, onClose, onDone }: { subject?: Subject; onClose: () => void; onDone: (s: Subject) => void }) {
  const [code, setCode] = useState(subject?.code ?? "");
  const [name, setName] = useState(subject?.name ?? "");
  const { busy, error, run } = useAction();
  const save = () => run(async () => {
    const s = subject
      ? await api<Subject>(`/api/v1/subjects/${subject.subject_id}`, { method: "PATCH", body: { name } })
      : await api<Subject>("/api/v1/subjects", { method: "POST", body: { code, name } });
    onDone(s);
  });
  return (
    <Modal title={subject ? `Edit ${subject.name}` : "Add subject"} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Code" value={code} disabled={!!subject} onChange={(e) => setCode(e.target.value.toUpperCase())}
        hint={subject ? "The code identifies the subject everywhere and cannot be changed." : "Capital letters, digits and underscore, e.g. PHYSICS"} />
      <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={code.length < 2 || name.trim().length < 2 || (!!subject && name === subject.name)} onClick={() => void save()}>{subject ? "Save" : "Add subject"}</Button>
      </div>
    </Modal>
  );
}

/** Subjects are never deleted: teachers, papers, exams and results keep referring to them. Deactivating removes a subject
 * from the active catalogue so no provider can newly offer it; everything that already uses it is unchanged. */
function ToggleSubject({ subject, onClose, onDone }: { subject: Subject; onClose: () => void; onDone: (s: Subject) => void }) {
  const [reason, setReason] = useState("");
  const { busy, error, run } = useAction();
  const deactivating = subject.status === "ACTIVE";
  return (
    <ConfirmDialog title={`${deactivating ? "Deactivate" : "Reactivate"} ${subject.name}`} danger={deactivating}
      confirmLabel={deactivating ? "Deactivate" : "Reactivate"} busy={busy} onCancel={onClose}
      message={deactivating
        ? "The subject leaves the active catalogue and no provider can start offering it. Providers that already offer it, their teachers, papers, exams and all results keep it. Nothing is deleted."
        : "The subject returns to the active catalogue and providers can offer it again."}
      onConfirm={() => void run(async () => onDone(await api<Subject>(`/api/v1/subjects/${subject.subject_id}`, { method: "PATCH", body: { status: deactivating ? "INACTIVE" : "ACTIVE", reason } })))}>
      {error && <Alert kind="error">{error}</Alert>}
      <TextAreaField label="Reason (required, recorded in the audit log)" value={reason} onChange={(e) => setReason(e.target.value)} />
    </ConfirmDialog>
  );
}

// ------------------------------------------------------------------ provider and branch staff

function ProviderSubjectsPage() {
  const { can } = useAuth();
  const { providerId } = useScope();
  const tabs = [
    ...(can("subject.manage") ? [{ id: "offered", label: "Offered subjects" }] : []),
    ...(can("teaching.assign") ? [{ id: "teaching", label: "Teaching assignments" }] : []),
  ];
  const [chosen, setChosen] = useState("");
  const tab = chosen || tabs[0]?.id;
  return (
    <>
      <PageHeader subtitle="Subjects come from the platform catalogue. Students take a subject through their batches (see Batches)." />
      <Tabs active={tab ?? ""} onChange={setChosen} tabs={tabs} />
      {tab === "offered" && (
        <>
          <ScopeBar branch={false} />
          {providerId ? <ProviderSubjects providerId={providerId} /> : <EmptyState title="No provider available" />}
        </>
      )}
      {tab === "teaching" && <NeedBranch>{(ids) => <TeachingAssignments {...ids} />}</NeedBranch>}
    </>
  );
}
