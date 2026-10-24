import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { AcademicFields } from "../components/classLevels";
import { type Academic, type AcademicForm, EMPTY_ACADEMIC, academicBody, academicErrors, academicText, classLabel, toForm } from "../services/classLevels";
import NeedBranch from "../components/NeedBranch";
import { StudentDirectory } from "../components/students";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, Tabs, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api, type Page } from "../services/api";

// batch_subjects: batch_id -> the subjects taken in that batch; a batch missing there takes every subject it offers.
type Enrollment = { enrollment_id: string; student_id: string; student_name: string | null; status: string; batch_ids: string[]; batch_subjects?: Record<string, string[]>; enrolled_at: string;
  academic?: Academic | null };
// default_*: optional Class / Academic Year a batch suggests when enrolling into it (prefill only).
type Batch = { batch_id: string; name: string; status: string; default_class_level?: string | null; default_academic_year?: string | null; default_other_description?: string | null };
type Subject = { subject_id: string; name: string };
type Transfer = { transfer_id: string; student_id: string; to_branch_id: string; status: string; reason: string | null };

/** Students & Enrollments: the global directory (one identity per student, with the enrollments the viewer manages) and the branch
 * roster where students are enrolled, grouped into batches and transferred. */
export default function StudentsPage() {
  const scope = useScope();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "directory";
  return (
    <>
      <Tabs active={tab} onChange={(id) => setParams({ tab: id }, { replace: true })}
        tabs={[{ id: "directory", label: "Directory" }, { id: "branch", label: "Branch enrollments" }]} />
      {tab === "directory" ? (
        <>
          <PageHeader subtitle="Every student identity with an enrollment you manage. Search by Student ID, email or phone." />
          <StudentDirectory providers={scope.providers} />
        </>
      ) : (
        <NeedBranch>{({ providerId, branchId }) => <Inner providerId={providerId} branchId={branchId} />}</NeedBranch>
      )}
    </>
  );
}

function Inner({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [enrolling, setEnrolling] = useState(false);
  const [moving, setMoving] = useState<Enrollment | null>(null);
  const [managing, setManaging] = useState<Enrollment | null>(null);
  const list = useApi<Page<Enrollment>>(`/api/v1/branches/${branchId}/enrollments?page=${page}&page_size=25`);
  const batches = useApi<Page<Batch>>(`/api/v1/branches/${branchId}/batches?page_size=100`);
  const transfers = useApi<Page<Transfer>>(can("enrollment.transfer_approve") ? `/api/v1/providers/${providerId}/transfers?status=PENDING` : null);
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const batchName = (id: string) => batches.data?.items.find((b) => b.batch_id === id)?.name ?? id.slice(0, 8);
  const subjectName = (id: string) => subjects.data?.find((s) => s.subject_id === id)?.name ?? id.slice(0, 8);
  const batchLabel = (e: Enrollment, id: string) => batchName(id) + (e.batch_subjects?.[id] ? ` (${e.batch_subjects[id].map(subjectName).join(", ")})` : "");
  const decide = useAction();
  return (
    <>
      <PageHeader subtitle="Enroll students into this branch and its batches; one global student identity across providers." actions={<Button onClick={() => setEnrolling(true)}>Enroll student</Button>} />
      <div className="stack">
        {(transfers.data?.items.length ?? 0) > 0 && (
          <Alert kind="warn">
            <strong>{transfers.data?.items.length} transfer request(s) await your approval</strong>
            {decide.error && <div>{decide.error}</div>}
            {transfers.data?.items.map((t) => (
              <div key={t.transfer_id} className="row" style={{ marginTop: 6 }}>
                <span>{t.student_id}: {t.reason}</span>
                <Button small onClick={() => void decide.run(async () => { await api(`/api/v1/transfers/${t.transfer_id}/approve`, { method: "POST", body: { note: "Approved by provider admin" } }); transfers.reload(); list.reload(); })}>Approve</Button>
                <Button small variant="secondary" onClick={() => void decide.run(async () => { await api(`/api/v1/transfers/${t.transfer_id}/reject`, { method: "POST", body: { note: "Rejected by provider admin" } }); transfers.reload(); })}>Reject</Button>
              </div>
            ))}
          </Alert>
        )}
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(e) => e.enrollment_id} empty={<EmptyState title="No students enrolled" hint="Enroll your first student." />}
              columns={[
                { header: "Student ID", render: (e) => e.student_id }, { header: "Name", render: (e) => e.student_name ?? "—" },
                { header: "Class", render: (e) => academicText(e.academic) || <span className="muted">Not set</span> },
                { header: "Batches", render: (e) => e.batch_ids.map((id) => batchLabel(e, id)).join(", ") || "—" },
                { header: "Status", render: (e) => <StatusBadge status={e.status} /> },
                { header: "", render: (e) => (
                  <div className="row">
                    <Button small variant="secondary" onClick={() => setManaging(e)}>Manage</Button>
                    {e.status === "ACTIVE" && <Button small variant="secondary" onClick={() => setMoving(e)}>Request transfer</Button>}
                  </div>
                ) },
              ]} />
            <Pagination page={page} pageSize={25} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </div>
      {enrolling && <EnrollDialog branchId={branchId} batches={batches.data?.items ?? []} onClose={() => setEnrolling(false)} onDone={() => { setEnrolling(false); list.reload(); }} />}
      {managing && <ManageDialog enrollment={managing} batches={batches.data?.items ?? []} onClose={() => setManaging(null)} onChanged={() => { list.reload(); }} />}
      {moving && <TransferDialog enrollment={moving} providerId={providerId} currentBranch={branchId} onClose={() => setMoving(null)} onDone={() => { setMoving(null); transfers.reload(); }} />}
    </>
  );
}

function EnrollDialog({ branchId, batches, onClose, onDone }: { branchId: string; batches: Batch[]; onClose: () => void; onDone: () => void }) {
  const [mode, setMode] = useState<"search" | "new">("search");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [matches, setMatches] = useState<{ student_id: string; name: string }[] | null>(null);
  const [picked, setPicked] = useState("");
  const [nf, setNf] = useState({ first_name: "", last_name: "", email: "", phone: "" });
  const [confirmDistinct, setConfirmDistinct] = useState(false);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [chosen, setChosen] = useState<Record<string, string[] | null>>({});
  const [ac, setAc] = useState<AcademicForm>(EMPTY_ACADEMIC);
  const [acTouched, setAcTouched] = useState(false);       // staff changed Class/Year themselves: batch defaults no longer prefill
  const [showErrors, setShowErrors] = useState(false);
  const [conflict, setConflict] = useState("");
  const search = useAction();
  const submit = useAction();
  const suggestion = batchDefaults(batches.filter((b) => batchIds.includes(b.batch_id)));
  const toggle = (id: string) => {
    const next = batchIds.includes(id) ? batchIds.filter((x) => x !== id) : [...batchIds, id];
    setBatchIds(next);
    if (!acTouched) {     // prefill from the selected batches' defaults until staff edit Class/Year; conflicting defaults prefill nothing
      const s = batchDefaults(batches.filter((b) => next.includes(b.batch_id)));
      setAc(s.kind === "one" ? s.form : EMPTY_ACADEMIC);
    }
  };
  const doEnroll = () => submit.run(async () => {
    setConflict("");
    if (Object.keys(academicErrors(ac, true)).length) { setShowErrors(true); return; }
    try {
      const batch_subjects = batchIds.filter((b) => chosen[b]).map((b) => ({ batch_id: b, subject_ids: chosen[b] }));
      const academic = academicBody(ac);
      const body = mode === "search" ? { student_id: picked, batch_ids: batchIds, batch_subjects, academic } : { new_student: { ...nf, phone: nf.phone || null }, confirm_distinct_person: confirmDistinct, batch_ids: batchIds, batch_subjects, academic };
      await api(`/api/v1/branches/${branchId}/enrollments`, { method: "POST", body });
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.code === "STUDENT_MATCH_FOUND") { setConflict(e.message); return; }
      throw e;
    }
  });
  return (
    <Modal title="Enroll a student" onClose={onClose}>
      <div className="tabs"><button aria-selected={mode === "search"} onClick={() => setMode("search")}>Existing student</button><button aria-selected={mode === "new"} onClick={() => setMode("new")}>New student</button></div>
      {mode === "search" ? (
        <>
          <p className="muted small">Search the global registry first so nobody gets a second Student ID.</p>
          <div className="grid cols-2"><Field label="Email" value={email} onChange={(e) => setEmail(e.target.value)} /><Field label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} /></div>
          {search.error && <Alert kind="error">{search.error}</Alert>}
          <Button variant="secondary" busy={search.busy} disabled={!email && !phone} onClick={() => void search.run(async () => { const r = await api<{ matches: { student_id: string; name: string }[] }>("/api/v1/students/lookup", { method: "POST", body: { branch_id: branchId, email: email || null, phone: phone || null } }); setMatches(r.matches); setPicked(r.matches[0]?.student_id ?? ""); })}>Search</Button>
          {matches && (matches.length === 0 ? <Alert kind="info">No match. Use “New student”.</Alert> : (
            <div role="radiogroup" aria-label="Matches" style={{ margin: "var(--space-3) 0" }}>
              {matches.map((m) => <label key={m.student_id} className="row"><input type="radio" name="m" checked={picked === m.student_id} onChange={() => setPicked(m.student_id)} />{m.student_id} — {m.name}</label>)}
            </div>
          ))}
        </>
      ) : (
        <>
          <div className="grid cols-2"><Field label="First name" value={nf.first_name} onChange={(e) => setNf({ ...nf, first_name: e.target.value })} /><Field label="Last name" value={nf.last_name} onChange={(e) => setNf({ ...nf, last_name: e.target.value })} /></div>
          <Field label="Email" type="email" value={nf.email} onChange={(e) => setNf({ ...nf, email: e.target.value })} />
          <Field label="Phone" value={nf.phone} onChange={(e) => setNf({ ...nf, phone: e.target.value })} />
          {conflict && <Alert kind="warn">{conflict}<label className="row"><input type="checkbox" checked={confirmDistinct} onChange={(e) => setConfirmDistinct(e.target.checked)} /> This is a different person</label></Alert>}
        </>
      )}
      <h3 style={{ marginTop: "var(--space-4)" }}>Batches</h3>
      {batches.filter((b) => b.status === "ACTIVE").map((b) => (
        <div key={b.batch_id}>
          <label className="row"><input type="checkbox" checked={batchIds.includes(b.batch_id)} onChange={() => toggle(b.batch_id)} />{b.name}</label>
          {batchIds.includes(b.batch_id) && <SubjectPicker batch={b} value={chosen[b.batch_id] ?? null} onChange={(v) => setChosen((c) => ({ ...c, [b.batch_id]: v }))} />}
        </div>
      ))}
      <h3 style={{ marginTop: "var(--space-4)" }}>Class and academic year</h3>
      {suggestion.kind === "conflict" && !acTouched && (
        <Alert kind="warn">The selected batches suggest different classes ({suggestion.labels.join("; ")}). Choose this student's class and academic year.</Alert>
      )}
      {suggestion.kind === "one" && !acTouched && <p className="muted small">Prefilled from the batch defaults ({suggestion.labels[0]}). Change them if this student differs.</p>}
      <AcademicFields value={ac} required showErrors={showErrors} onChange={(v) => { setAc(v); setAcTouched(true); }} />
      {submit.error && <Alert kind="error">{submit.error}</Alert>}
      <div style={{ height: "var(--space-3)" }} />
      <Button busy={submit.busy} onClick={() => void doEnroll()} disabled={mode === "search" ? !picked : !(nf.first_name && nf.last_name && nf.email)}>Enroll</Button>
    </Modal>
  );
}

function TransferDialog({ enrollment, providerId, currentBranch, onClose, onDone }: { enrollment: Enrollment; providerId: string; currentBranch: string; onClose: () => void; onDone: () => void }) {
  const branches = useApi<Page<{ branch_id: string; name: string }>>(`/api/v1/providers/${providerId}/branches?page_size=100`);
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const { busy, error, run } = useAction();
  return (
    <ConfirmDialog title={`Transfer ${enrollment.student_id}`} message="A Provider Admin must approve cross-branch transfers. The old enrollment is kept as history." confirmLabel="Request transfer" busy={busy}
      onCancel={onClose} onConfirm={() => void run(async () => { await api(`/api/v1/enrollments/${enrollment.enrollment_id}/transfers`, { method: "POST", body: { to_branch_id: to, reason } }); onDone(); })}>
      {error && <Alert kind="error">{error}</Alert>}
      <SelectField label="Target branch" value={to} onChange={(e) => setTo(e.target.value)}><option value="">Select…</option>{(branches.data?.items ?? []).filter((b) => b.branch_id !== currentBranch).map((b) => <option key={b.branch_id} value={b.branch_id}>{b.name}</option>)}</SelectField>
      <TextAreaField label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
    </ConfirmDialog>
  );
}

/** Batch membership and enrollment status of one student in this branch. Every change is a separate audited API call; the
 * history is kept server-side (enrollment_events), and the Student Portal sees the change after the next reference sync. */
function ManageDialog({ enrollment, batches, onClose, onChanged }: { enrollment: Enrollment; batches: Batch[]; onClose: () => void; onChanged: () => void }) {
  const [current, setCurrent] = useState<Enrollment>(enrollment);
  const [reason, setReason] = useState("");
  const [saved, setSaved] = useState("");
  const { busy, error, run } = useAction();
  const active = current.status === "ACTIVE";
  const toggleBatch = (batchId: string, member: boolean) => void run(async () => {
    setSaved("");
    await api(`/api/v1/enrollments/${current.enrollment_id}/batches/${batchId}`, { method: member ? "DELETE" : "PUT" });
    setCurrent((c) => ({ ...c, batch_ids: member ? c.batch_ids.filter((b) => b !== batchId) : [...c.batch_ids, batchId] }));
    setSaved(member ? "Removed from batch." : "Added to batch.");
    onChanged();
  });
  const setSubjects = (batchId: string, subject_ids: string[] | null) => void run(async () => {
    setSaved("");
    await api(`/api/v1/enrollments/${current.enrollment_id}/batches/${batchId}`, { method: "PUT", body: { subject_ids } });
    setCurrent((c) => {
      const next = { ...(c.batch_subjects ?? {}) };
      if (subject_ids) next[batchId] = subject_ids; else delete next[batchId];
      return { ...c, batch_subjects: next };
    });
    setSaved("Subjects updated.");
    onChanged();
  });
  const [acForm, setAcForm] = useState<AcademicForm>(toForm(enrollment.academic));
  const [acErrors, setAcErrors] = useState(false);
  const saveAcademic = () => void run(async () => {
    setSaved("");
    if (Object.keys(academicErrors(acForm, true)).length) { setAcErrors(true); return; }
    const out = await api<Academic>(`/api/v1/enrollments/${current.enrollment_id}/academic-profile`, { method: "PATCH", body: academicBody(acForm) });
    setCurrent((c) => ({ ...c, academic: out }));      // batches and subject selections are untouched
    setAcForm(toForm(out));
    setAcErrors(false);
    setSaved("Class and academic year saved.");
    onChanged();
  });
  const sendSetupLink = () => void run(async () => {
    setSaved("");
    const out = await api<{ expires_at: string | null }>(`/api/v1/enrollments/${current.enrollment_id}/password-setup-link`, { method: "POST" });
    setSaved(`Password setup link sent to the student's email${out.expires_at ? ` (valid until ${new Date(out.expires_at).toLocaleTimeString()})` : ""}.`);
  });
  const setStatus = (status: "ACTIVE" | "INACTIVE") => void run(async () => {
    setSaved("");
    const out = await api<Enrollment>(`/api/v1/enrollments/${current.enrollment_id}`, { method: "PATCH", body: { status, reason } });
    setCurrent((c) => ({ ...c, status: out.status }));
    setReason("");
    setSaved(status === "ACTIVE" ? "Enrollment reactivated." : "Enrollment deactivated.");
    onChanged();
  });
  return (
    <Modal title={`Enrollment of ${current.student_name ?? current.student_id}`} onClose={onClose}>
      <p className="muted small">Student ID {current.student_id} · <StatusBadge status={current.status} /></p>
      {error && <Alert kind="error">{error}</Alert>}
      {saved && <Alert kind="success">{saved}</Alert>}
      <h3>Class and academic year</h3>
      {current.academic ? <p className="small">Saved: <strong>{academicText(current.academic)}</strong></p> : (
        <Alert kind="warn">No class / academic year yet (this enrollment was made before classes were recorded). Class-specific exams do not reach this student until you set them.</Alert>
      )}
      <AcademicFields value={acForm} required showErrors={acErrors} disabled={busy} onChange={setAcForm} />
      <Button small busy={busy} onClick={saveAcademic}>Save class</Button>
      <h3 style={{ marginTop: "var(--space-4)" }}>Batches</h3>
      {!active && <p className="muted small">Reactivate the enrollment to change its batches.</p>}
      {batches.filter((b) => b.status === "ACTIVE" || current.batch_ids.includes(b.batch_id)).map((b) => {
        const member = current.batch_ids.includes(b.batch_id);
        return (
          <div key={b.batch_id}>
            <label className="row">
              <input type="checkbox" checked={member} disabled={busy || (!active && !member) || (!member && b.status !== "ACTIVE")} onChange={() => toggleBatch(b.batch_id, member)} />
              {b.name}{b.status !== "ACTIVE" ? " (inactive batch)" : ""}
            </label>
            {member && <SubjectPicker batch={b} value={current.batch_subjects?.[b.batch_id] ?? null} disabled={busy || !active || b.status !== "ACTIVE"} onChange={(v) => setSubjects(b.batch_id, v)} />}
          </div>
        );
      })}
      <h3 style={{ marginTop: "var(--space-4)" }}>Password</h3>
      <p className="muted small">Email the student a link to set (or reset) their Student Portal password. At most 3 links per student per hour.</p>
      <Button small variant="secondary" busy={busy} disabled={!active} onClick={sendSetupLink}>Send password setup link</Button>
      <h3 style={{ marginTop: "var(--space-4)" }}>{active ? "Deactivate enrollment" : "Reactivate enrollment"}</h3>
      <p className="muted small">{active ? "The student stops receiving new exams from this branch. Past attempts and results are kept." : "The student becomes eligible for future exams of this branch again."}</p>
      <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button variant={active ? "danger" : "primary"} busy={busy} disabled={reason.trim().length < 5} onClick={() => setStatus(active ? "INACTIVE" : "ACTIVE")}>
        {active ? "Deactivate" : "Reactivate"}
      </Button>
    </Modal>
  );
}

/** The subjects a student takes in one batch: every subject the batch offers (null, the default) or only the ticked ones. Exams of
 * this batch reach the student only if they take all of the exam's subjects. At least one subject stays ticked. */
function SubjectPicker({ batch, value, disabled, onChange }: { batch: Batch; value: string[] | null; disabled?: boolean; onChange: (v: string[] | null) => void }) {
  const offered = useApi<Subject[]>(`/api/v1/batches/${batch.batch_id}/subjects`);
  if (offered.loading || offered.error) return null;
  const all = (offered.data ?? []).map((s) => s.subject_id);
  if (!all.length) return <p className="muted small" style={{ marginLeft: 24 }}>This batch has no subjects yet.</p>;
  const taken = value ?? all;
  const toggle = (id: string) => {
    const next = taken.includes(id) ? taken.filter((x) => x !== id) : [...taken, id];
    if (next.length) onChange(all.every((s) => next.includes(s)) ? null : next);
  };
  return (
    <div role="group" aria-label={`Subjects in ${batch.name}`} style={{ marginLeft: 24 }}>
      {(offered.data ?? []).map((s) => {
        const on = taken.includes(s.subject_id);
        return <label key={s.subject_id} className="row"><input type="checkbox" checked={on} disabled={disabled || (on && taken.length === 1)} onChange={() => toggle(s.subject_id)} />{s.name}</label>;
      })}
    </div>
  );
}

type Suggestion = { kind: "none" } | { kind: "one"; form: AcademicForm; labels: string[] } | { kind: "conflict"; labels: string[] };

/** What the selected batches suggest for Class / Academic Year: nothing, one consistent default, or conflicting defaults (never resolved silently). */
function batchDefaults(selected: Batch[]): Suggestion {
  const withDefaults = selected.filter((b) => b.default_class_level || b.default_academic_year);
  const key = (b: Batch) => `${b.default_class_level ?? ""}|${b.default_other_description ?? ""}|${b.default_academic_year ?? ""}`;
  const describe = (b: Batch) => `${b.name}: ${[b.default_class_level ? classLabel(b.default_class_level) + (b.default_other_description ? ` (${b.default_other_description})` : "") : null, b.default_academic_year].filter(Boolean).join(", ")}`;
  if (!withDefaults.length) return { kind: "none" };
  if (new Set(withDefaults.map(key)).size > 1) return { kind: "conflict", labels: withDefaults.map(describe) };
  const b = withDefaults[0];
  return { kind: "one", labels: [describe(b)], form: { class_level: b.default_class_level ?? "", academic_year: b.default_academic_year ?? "", other_class_description: b.default_other_description ?? "" } };
}
