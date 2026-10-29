import { useState } from "react";
import { Alert, DataTable, EmptyState, Modal, Pagination, Spinner, StatusBadge } from "./ui";
import { useApi } from "../hooks/useApi";

// A student count that opens the students it counts. `url` is an authorized list endpoint returning { items: [{ student_id, name,
// status? }], total } for EXACTLY the group behind the count (the backend applies the same filters and the caller's scope); the list is
// fetched page by page from the server only when opened.

type Row = { student_id: string; name?: string | null; status?: string };
type ListPage = { items: Row[]; total: number };

const PAGE = 25;

export function StudentCount({ count, url, title, context }: { count: number; url: string; title: string; context: string }) {
  const [open, setOpen] = useState(false);
  if (!count) return <>{count}</>;
  return (
    <>
      <button type="button" className="link-button" onClick={() => setOpen(true)} aria-label={`${count} students: ${title}. Show the list`}>{count}</button>
      {open && <StudentListModal url={url} title={title} context={context} onClose={() => setOpen(false)} />}
    </>
  );
}

export function StudentListModal({ url, title, context, onClose }: { url: string; title: string; context: string; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const list = useApi<ListPage>(`${url}${url.includes("?") ? "&" : "?"}page=${page}&page_size=${PAGE}`);
  const withStatus = (list.data?.items ?? []).some((r) => r.status);
  return (
    <Modal title={title} onClose={onClose}>
      <p className="muted small">{context}</p>
      {list.loading && !list.data && <Spinner />}
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.data && (
        <>
          <p className="small">{list.data.total} student{list.data.total === 1 ? "" : "s"}</p>
          <DataTable rows={list.data.items} rowKey={(r) => r.student_id} empty={<EmptyState title="No students in this group" />}
            columns={[{ header: "Student ID", render: (r) => r.student_id }, { header: "Name", render: (r) => r.name ?? "—" },
                      ...(withStatus ? [{ header: "Status", render: (r: Row) => (r.status ? <StatusBadge status={r.status} /> : "—") }] : [])]} />
          <Pagination page={page} pageSize={PAGE} total={list.data.total} onChange={setPage} />
        </>
      )}
    </Modal>
  );
}
