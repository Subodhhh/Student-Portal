import { useState } from "react";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";
import { Alert, Button, DataTable, EmptyState, Modal, Pagination, SearchField, SelectField, Spinner, StatusBadge } from "./ui";

export type DirEnrollment = {
  enrollment_id: string; provider_id: string; provider_name: string; branch_id: string; branch_name: string; status: string; enrolled_at: string; batches: string[];
};
export type DirStudent = { student_id: string; name: string | null; account_status: string | null; enrollments: DirEnrollment[] };
type Detail = {
  profile: { student_id: string; first_name: string | null; last_name: string | null; email: string | null; phone: string | null; account_status: string | null };
  enrollments: DirEnrollment[];
  exams: { exam_id: string; name: string; exam_status: string; assignment_status: string; start_at: string; end_at: string }[];
};

const day = (s: string) => new Date(s).toLocaleDateString();

/** Global student identities with the enrollments the viewer manages (one row per student, never a duplicate identity). */
export function StudentDirectory({ providerId, branchId, providers }: { providerId?: string; branchId?: string; providers?: { provider_id: string; name: string }[] }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState("");
  const [open, setOpen] = useState<string | null>(null);
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
              { header: "Enrollments", render: (s) => s.enrollments.map((e) => `${e.provider_name} / ${e.branch_name}`).join("; ") || "—" },
              { header: "Batches", render: (s) => [...new Set(s.enrollments.flatMap((e) => e.batches))].join(", ") || "—" },
              { header: "Status", render: (s) => <StatusBadge status={s.enrollments.some((e) => e.status === "ACTIVE") ? "ACTIVE" : "INACTIVE"} /> },
              { header: "Actions", render: (s) => <Button small variant="secondary" onClick={() => setOpen(s.student_id)} aria-label={`View ${s.student_id}`}>View</Button> },
            ]} />
          <Pagination page={page} pageSize={25} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
      <p className="muted small">One global identity per student: enrolling a student with another provider adds an enrollment, never a second Student ID.</p>
      {open && <StudentDetail studentId={open} onClose={() => setOpen(null)} />}
    </div>
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
