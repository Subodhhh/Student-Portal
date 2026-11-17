import { useState } from "react";
import { Alert, DataTable, EmptyState, Modal, Pagination, Spinner, StatusBadge } from "./ui";
import { useApi } from "../hooks/useApi";

type Row = { student_id: string; name?: string | null; status?: string };
type ListPage = { items: Row[]; total: number };

const PAGE_SIZE = 25;

export function StudentCount({ count, url, title, context }: { count: number; url: string; title: string; context: string }) {
  const [open, setOpen] = useState(false);

  // If the count is 0, just render the number without any button/interactivity
  if (!count) return <span>{count}</span>;

  return (
    <>
      <button 
        type="button" 
        className="link-button" 
        onClick={() => setOpen(true)} 
        aria-label={`${count} students: ${title}. Show the list`}
        style={{ 
          fontWeight: 600, 
          textDecoration: "underline", 
          cursor: "pointer",
          background: "none",
          border: "none",
          padding: 0,
          color: "inherit",
          font: "inherit"
        }}
      >
        {count}
      </button>
      
      {open && (
        <StudentListModal 
          url={url} 
          title={title} 
          context={context} 
          onClose={() => setOpen(false)} 
        />
      )}
    </>
  );
}

export function StudentListModal({ url, title, context, onClose }: { url: string; title: string; context: string; onClose: () => void }) {
  const [page, setPage] = useState(1);
  
  // Ensure the URL has the correct page parameters without mutating the original
  const separator = url.includes("?") ? "&" : "?";
  const list = useApi<ListPage>(`${url}${separator}page=${page}&page_size=${PAGE_SIZE}`);
  
  const withStatus = (list.data?.items ?? []).some((r) => r.status);

  return (
    <Modal title={title} onClose={onClose}>
      <p className="muted small" style={{ marginBottom: "1rem" }}>{context}</p>
      
      {list.loading && !list.data && <Spinner label="Loading students..." />}
      {list.error && <Alert kind="error">{list.error}</Alert>}
      
      {list.data && (
        <>
          <p className="small" style={{ marginBottom: "1rem", fontWeight: 500 }}>
            {list.data.total} student{list.data.total === 1 ? "" : "s"} found
          </p>
          
          <DataTable 
            rows={list.data.items} 
            rowKey={(r) => r.student_id} 
            empty={<EmptyState title="No students in this group" />}
            columns={[
              { header: "Student ID", render: (r) => <code>{r.student_id}</code> }, 
              { header: "Name", render: (r) => r.name ?? "—" },
              ...(withStatus ? [{ header: "Status", render: (r: Row) => (r.status ? <StatusBadge status={r.status} /> : "—") }] : [])
            ]} 
          />
          
          <div style={{ marginTop: "1rem" }}>
            <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onChange={setPage} />
          </div>
        </>
      )}
    </Modal>
  );
}