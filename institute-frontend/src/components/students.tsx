import { useState } from "react";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";
import { Alert, Button, DataTable, EmptyState, Modal, Pagination, SearchField, SelectField, Spinner, StatusBadge } from "./ui";

export type DirSubject = { subject_id: string; subject: string | null; batch: string; chosen: boolean };
export type DirEnrollment = {
  enrollment_id: string; provider_id: string; provider_name: string; branch_id: string; branch_name: string; status: string; enrolled_at: string; batches: string[];
  subjects?: DirSubject[];   
};

type Pair = { key: string; subject: string; provider: string; branch: string; batch: string; chosen: boolean; active: boolean };

export function SubjectProviders({ student }: { student: DirStudent }) {
  const [open, setOpen] = useState(false);
  const pairs: Pair[] = student.enrollments.flatMap((e) => (e.subjects ?? []).map((x) => ({
    key: `${e.enrollment_id}-${x.subject_id}-${x.batch}`, subject: x.subject ?? "Subject", provider: e.provider_name, branch: e.branch_name, batch: x.batch,
    chosen: x.chosen, active: e.status === "ACTIVE" })));
  const none = student.enrollments.filter((e) => !(e.subjects ?? []).length);
  
  if (!pairs.length && !none.length) return <>—</>;
  
  const providersOf = (subject: string) => new Set(pairs.filter((x) => x.subject === subject && x.active).map((x) => x.provider)).size;
  const lines = [...pairs].sort((a, b) => a.subject.localeCompare(b.subject) || Number(b.active) - Number(a.active));
  const shown = lines.slice(0, 3);
  
  return (
    <div className="subject-providers" style={{ display: "flex", flexDirection: "column", gap: "4px", maxWidth: "300px" }}>
      {shown.map((x) => (
        <div key={x.key} className={x.active ? undefined : "muted"} style={{ fontSize: "0.85rem" }}>
          <strong>{x.subject}</strong> — {x.provider}
          {!x.active && " (inactive)"}
          {x.active && providersOf(x.subject) > 1 && <span className="muted small"> · taught by {providersOf(x.subject)} providers</span>}
        </div>
      ))}
      {none.map((e) => <div key={e.enrollment_id} className="muted small" style={{ fontSize: "0.85rem" }}>{e.provider_name} / {e.branch_name}: no subject assigned</div>)}
      {lines.length > 3 && <button type="button" className="link-button small" onClick={() => setOpen(true)}>+{lines.length - 3} more</button>}
      
      {open && (
        <Modal title={`Subjects of ${student.name ?? student.student_id}`} onClose={() => setOpen(false)}>
          <p className="muted small">From the student's batch memberships in the institutes you manage.</p>
          <DataTable rows={lines} rowKey={(x) => x.key} columns={[
            { header: "Subject", render: (x) => x.subject }, 
            { header: "Provider", render: (x) => x.provider },
            { header: "Branch / batch", render: (x) => `${x.branch} · ${x.batch}` },
            { header: "Mapping", render: (x) => (x.chosen ? "Chosen in the batch" : "All subjects of the batch") },
            { header: "Enrollment", render: (x) => <StatusBadge status={x.active ? "ACTIVE" : "INACTIVE"} /> }
          ]} />
        </Modal>
      )}
    </div>
  );
}

export type DirStudent = { student_id: string; name: string | null; account_status: string | null; enrollments: DirEnrollment[] };
type Detail = {
  profile: { student_id: string; first_name: string | null; last_name: string | null; email: string | null; phone: string | null; account_status: string | null };
  enrollments: DirEnrollment[];
  exams: { exam_id: string; name: string; exam_status: string; assignment_status: string; start_at: string; end_at: string }[];
};

const day = (s: string) => new Date(s).toLocaleDateString();

export function StudentDirectory({ providerId, branchId, providers }: { providerId?: string; branchId?: string; providers?: { provider_id: string; name: string }[] }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState("");
  
  // Modals state
  const [openProfile, setOpenProfile] = useState<string | null>(null);
  const [openBatches, setOpenBatches] = useState<DirStudent | null>(null); // New state for Batches modal

  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  if (q.trim().length >= 3) params.set("q", q.trim());
  if (status) params.set("status", status);
  if (providerId || provider) params.set("provider_id", providerId || provider);
  if (branchId) params.set("branch_id", branchId);
  
  const list = useApi<Page<DirStudent>>(`/api/v1/students?${params.toString()}`);
  
  return (
    <div className="stack">
      <div className="toolbar">
        <SearchField label="Student ID, email or phone" value={q} placeholder="STU000123, name@example.com or 98765…" onChange={(v) => { setQ(v); setPage(1); }} />
        {!providerId && providers && providers.length > 1 && (
          <SelectField label="Provider" value={provider} onChange={(e) => { setProvider(e.target.value); setPage(1); }}>
            <option value="">All providers</option>
            {providers.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
          </SelectField>
        )}
        <SelectField label="Enrollment status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All</option><option value="ACTIVE">Active</option><option value="INACTIVE">Inactive</option>
        </SelectField>
      </div>
      
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(s) => s.student_id}
            empty={<EmptyState title={q || status || provider ? "No students match" : "No enrolled students"} hint={q && q.trim().length < 3 ? "Type at least 3 characters." : undefined} />}
            columns={[
              { header: "Student ID", render: (s) => s.student_id },
              { header: "Name", render: (s) => s.name ?? <span className="muted">unavailable</span> },
              { header: "Enrollments (subject — provider)", render: (s) => <SubjectProviders student={s} /> },
              { header: "Batches", render: (s) => {
                  const allBatches = s.enrollments.flatMap((e) => e.batches.map((b) => ({ batch: b, provider: e.provider_name, branch: e.branch_name })));
                  const uniqueBatches = Array.from(new Map(allBatches.map((item) => [item.batch, item])).values());
                  if (uniqueBatches.length === 0) return "—";
                  return (
                    <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                      {uniqueBatches.map((b, i) => (
                        <div key={i} style={{ fontSize: "0.85rem" }}>
                          <strong>{b.batch}</strong>
                          <span className="muted small"> ({b.provider})</span>
                        </div>
                      ))}
                    </div>
                  );
                } 
              },
              { header: "Status", render: (s) => <StatusBadge status={s.enrollments.some((e) => e.status === "ACTIVE") ? "ACTIVE" : "INACTIVE"} /> },
              { header: "Actions", render: (s) => (
                  <div className="row">
                    <Button small variant="secondary" onClick={() => setOpenProfile(s.student_id)}>View Profile</Button>
                    <Button small variant="secondary" onClick={() => setOpenBatches(s)}>Batches & Subjects</Button>
                  </div>
                ) 
              },
            ]} />
          <Pagination page={page} pageSize={25} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
      <p className="muted small">One global identity per student: enrolling a student with another provider adds an enrollment, never a second Student ID.</p>
      
      {/* Profile Modal */}
      {openProfile && <StudentDetail studentId={openProfile} onClose={() => setOpenProfile(null)} />}
      
      {/* Batches & Subjects Modal */}
      {openBatches && <StudentBatchesModal student={openBatches} onClose={() => setOpenBatches(null)} />}
    </div>
  );
}

// --- NEW MODAL: Registered Batches and Offered Subjects ---
function StudentBatchesModal({ student, onClose }: { student: DirStudent; onClose: () => void }) {
  return (
    <Modal title={`Batches & Subjects: ${student.name ?? student.student_id}`} onClose={onClose}>
      <div className="stack">
        {student.enrollments.map((e) => (
          <div key={e.enrollment_id} style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 16, background: "#f9fafb" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>{e.provider_name} <span className="muted" style={{ fontWeight: "normal" }}>· {e.branch_name}</span></h3>
              <StatusBadge status={e.status} />
            </div>
            
            {e.batches.length === 0 ? (
              <p className="muted small" style={{ margin: 0 }}>No batches registered for this branch.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {e.batches.map((batchName) => {
                  const batchSubjects = e.subjects?.filter((s) => s.batch === batchName) || [];
                  return (
                    <div key={batchName} style={{ background: "white", border: "1px solid #e5e7eb", borderRadius: 6, padding: 12 }}>
                      <div style={{ fontWeight: 600, fontSize: "0.95rem", marginBottom: 8, color: "#111827" }}>Batch: {batchName}</div>
                      {batchSubjects.length === 0 ? (
                        <span className="muted small">No subjects assigned to this batch.</span>
                      ) : (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                          {batchSubjects.map((s) => (
                            <span key={s.subject_id} style={{ 
                              fontSize: "0.85rem", 
                              padding: "4px 8px", 
                              borderRadius: 4, 
                              background: s.chosen ? "#dbeafe" : "#f3f4f6",
                              color: s.chosen ? "#1e40af" : "#4b5563",
                              border: `1px solid ${s.chosen ? "#bfdbfe" : "#e5e7eb"}`
                            }}>
                              {s.subject} {s.chosen ? "(Chosen)" : "(All offered)"}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}
        {student.enrollments.length === 0 && (
          <EmptyState title="No enrollments found" hint="This student is not enrolled in any branches you manage." />
        )}
      </div>
      <div className="modal-actions" style={{ marginTop: 20 }}>
        <Button variant="secondary" onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}

function StudentDetail({ studentId, onClose }: { studentId: string; onClose: () => void }) {
  const d = useApi<Detail>(`/api/v1/students/${studentId}`);
  const p = d.data?.profile;
  return (
    <Modal title={p ? `${[p.first_name, p.last_name].filter(Boolean).join(" ") || studentId} (${studentId})` : studentId} onClose={onClose}>
      {d.loading && !d.data ? <Spinner /> : d.error ? <Alert kind="error">{d.error}</Alert> : d.data && (
        <div className="stack">
          <section>
            <h3>Global profile</h3>
            <p className="small">{p?.email ?? "—"} · {p?.phone ?? "no phone"} · account {p?.account_status ? <StatusBadge status={p.account_status} /> : "—"}</p>
            <p className="muted small">The profile belongs to the student; providers see it only while they enroll the student.</p>
          </section>
          <section>
            <h3>Enrollments you manage</h3>
            <DataTable rows={d.data.enrollments} rowKey={(e) => e.enrollment_id}
              columns={[{ header: "Provider", render: (e) => e.provider_name }, { header: "Branch", render: (e) => e.branch_name },
                { header: "Batches", render: (e) => e.batches.join(", ") || "—" }, { header: "Since", render: (e) => day(e.enrolled_at) },
                { header: "Status", render: (e) => <StatusBadge status={e.status} /> }]} />
          </section>
          <section>
            <h3>Exams</h3>
            <DataTable rows={d.data.exams} rowKey={(e) => e.exam_id} empty={<EmptyState title="No exams assigned yet" />}
              columns={[{ header: "Exam", render: (e) => e.name }, { header: "Starts", render: (e) => new Date(e.start_at).toLocaleString() },
                { header: "Attempt", render: (e) => <StatusBadge status={e.assignment_status} /> }]} />
          </section>
          <div className="modal-actions"><Button variant="secondary" onClick={onClose}>Close</Button></div>
        </div>
      )}
    </Modal>
  );
}