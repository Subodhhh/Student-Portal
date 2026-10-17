import { useState } from "react";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";
import { Alert, Button, DataTable, EmptyState, Pagination, Spinner, StatusBadge } from "./ui";

export type AuditRow = {
  audit_id: string; at: string; actor: string | null; actor_code: string | null; actor_name: string | null; action: string;
  target_type: string | null; target_id: string | null; provider_id: string | null; provider_name: string | null; outcome: string; reason: string | null;
};

const when = (s: string) => new Date(s).toLocaleString();

/** A page of the (read-only) audit log for the given filters, e.g. one provider's or one account's history. */
export function AuditTable({ filters, pageSize = 20, emptyTitle = "No matching entries", showProvider = true }:
  { filters: Record<string, string>; pageSize?: number; emptyTitle?: string; showProvider?: boolean }) {
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)), page: String(page), page_size: String(pageSize) });
  const list = useApi<Page<AuditRow>>(`/api/v1/audit-logs?${params.toString()}`);
  if (list.loading && !list.data) return <Spinner />;
  return (
    <div className="stack">
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      <DataTable rows={list.data?.items ?? []} rowKey={(a) => a.audit_id} empty={<EmptyState title={emptyTitle} />}
        columns={[
          { header: "When", render: (a) => when(a.at) },
          { header: "Actor", render: (a) => a.actor_name ? <>{a.actor_name} <span className="muted small">{a.actor_code}</span></> : a.actor ?? "system" },
          { header: "Action", render: (a) => <code>{a.action}</code> },
          { header: "Target", render: (a) => a.target_type ? `${a.target_type}${a.target_id ? ` · ${a.target_id.slice(0, 8)}` : ""}` : "—" },
          ...(showProvider ? [{ header: "Provider", render: (a: AuditRow) => a.provider_name ?? "—" }] : []),
          { header: "Outcome", render: (a) => <StatusBadge status={a.outcome} /> },
          { header: "Reason", render: (a) => a.reason ?? "" },
        ]} />
      <Pagination page={page} pageSize={pageSize} total={list.data?.total ?? 0} onChange={setPage} />
    </div>
  );
}
