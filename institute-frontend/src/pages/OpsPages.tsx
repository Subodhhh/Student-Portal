import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { Alert, Button, Card, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

const when = (s: string) => new Date(s).toLocaleString();

// ---------------------------------------------------------------- notifications
type N = { notification_id: string; kind: string; title: string; body: string; created_at: string; read_at: string | null };
export function NotificationsPage() {
  const [page, setPage] = useState(1);
  const list = useApi<Page<N>>(`/api/v1/notifications?page=${page}&page_size=20`);
  const act = useAction();
  return (
    <>
      <PageHeader title="Notifications" actions={<Button variant="secondary" busy={act.busy} onClick={() => void act.run(async () => { await api("/api/v1/notifications/read-all", { method: "POST" }); list.reload(); })}>Mark all read</Button>} />
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading ? <Spinner /> : (
        <div className="stack">
          {(list.data?.items ?? []).length === 0 && <Card><EmptyState title="You're all caught up" /></Card>}
          {(list.data?.items ?? []).map((n) => (
            <Card key={n.notification_id} hover>
              <div className="row between"><strong>{n.title}</strong><span className="muted small">{when(n.created_at)}</span></div>
              <p style={{ whiteSpace: "pre-wrap" }}>{n.body}</p>
              <div className="row"><StatusBadge status={n.read_at ? "SENT" : "PENDING"} />{!n.read_at && <Button small variant="secondary" onClick={() => void act.run(async () => { await api(`/api/v1/notifications/${n.notification_id}/read`, { method: "POST" }); list.reload(); })}>Mark read</Button>}</div>
            </Card>
          ))}
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- support
type T = { ticket_id: string; subject: string; status: string; priority: string; created_at: string };
type TD = { ticket: T; messages: { author: string; body: string; at: string }[] };
export function SupportPage() {
  const { can } = useAuth();
  const { providerId } = useScope();
  const [page, setPage] = useState(1);
  const list = useApi<Page<T>>(`/api/v1/support/tickets?page=${page}&page_size=20`);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [f, setF] = useState({ subject: "", body: "", priority: "NORMAL" });
  const { busy, error, run } = useAction();
  return (
    <>
      <PageHeader title="Support" subtitle="Tickets you raised, or that your scope can manage" actions={<Button onClick={() => setCreating(true)}>New ticket</Button>} />
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading ? <Spinner /> : (
        <div className="stack">
          <DataTable rows={list.data?.items ?? []} rowKey={(t) => t.ticket_id} empty={<EmptyState title="No tickets" />}
            columns={[{ header: "Subject", render: (t) => <button className="link-btn" onClick={() => setOpen(t.ticket_id)}>{t.subject}</button> }, { header: "Priority", render: (t) => t.priority }, { header: "Status", render: (t) => <StatusBadge status={t.status} /> }, { header: "Opened", render: (t) => when(t.created_at) }]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </div>
      )}
      {creating && (
        <Modal title="New support ticket" onClose={() => setCreating(false)}>
          {error && <Alert kind="error">{error}</Alert>}
          <Field label="Subject" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} />
          <SelectField label="Priority" value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option>LOW</option><option>NORMAL</option><option>HIGH</option></SelectField>
          <TextAreaField label="Describe the problem" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} />
          <Button busy={busy} disabled={f.subject.length < 3 || !f.body} onClick={() => void run(async () => { await api("/api/v1/support/tickets", { method: "POST", body: { ...f, provider_id: providerId || null } }); setCreating(false); list.reload(); })}>Submit</Button>
        </Modal>
      )}
      {open && <TicketDialog id={open} canManage={can("support.manage")} onClose={() => { setOpen(null); list.reload(); }} />}
    </>
  );
}

function TicketDialog({ id, canManage, onClose }: { id: string; canManage: boolean; onClose: () => void }) {
  const d = useApi<TD>(`/api/v1/support/tickets/${id}`);
  const [msg, setMsg] = useState("");
  const act = useAction();
  return (
    <Modal title={d.data?.ticket.subject ?? "Ticket"} onClose={onClose}>
      {d.loading && <Spinner />}{d.error && <Alert kind="error">{d.error}</Alert>}{act.error && <Alert kind="error">{act.error}</Alert>}
      {d.data && (
        <>
          <p><StatusBadge status={d.data.ticket.status} /></p>
          {d.data.messages.map((m, i) => <div key={i} className="card" style={{ marginBottom: 8 }}><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{m.body}</p><span className="muted small">{when(m.at)}</span></div>)}
          {d.data.ticket.status !== "CLOSED" && (
            <>
              <TextAreaField label="Reply" value={msg} onChange={(e) => setMsg(e.target.value)} />
              <div className="row">
                <Button busy={act.busy} disabled={!msg} onClick={() => void act.run(async () => { await api(`/api/v1/support/tickets/${id}/messages`, { method: "POST", body: { body: msg } }); setMsg(""); d.reload(); })}>Send</Button>
                {canManage && d.data.ticket.status !== "RESOLVED" && <Button variant="secondary" onClick={() => void act.run(async () => { await api(`/api/v1/support/tickets/${id}`, { method: "PATCH", body: { status: "RESOLVED" } }); d.reload(); })}>Mark resolved</Button>}
                <Button variant="secondary" onClick={() => void act.run(async () => { await api(`/api/v1/support/tickets/${id}`, { method: "PATCH", body: { status: "CLOSED" } }); d.reload(); })}>Close ticket</Button>
              </div>
            </>
          )}
        </>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- billing
type Inv = { invoice_id: string; invoice_number: string; amount: string; due_on: string; status: string; paid: string; refunded: string; balance: string };
type InvDetail = Inv & { payments: { amount: string; method: string; reference: string; at: string }[]; adjustments: { kind: string; amount: string; reason: string; at: string }[] };
const key = () => crypto.randomUUID();

export function BillingPage() {
  const { can, me } = useAuth();
  const { providerId } = useScope();
  const [page, setPage] = useState(1);
  const sub = useApi<{ subscription: { plan: string; price: string; starts_on: string; ends_on: string | null } | null }>(providerId ? `/api/v1/providers/${providerId}/subscription` : null);
  const list = useApi<Page<Inv>>(providerId ? `/api/v1/providers/${providerId}/invoices?page=${page}&page_size=20` : null);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [f, setF] = useState({ amount: "", due_on: "" });
  const { busy, error, run } = useAction();
  return (
    <>
      <PageHeader title="Billing" subtitle="Subscription, invoices and payment history" actions={can("billing.routine") && <Button onClick={() => setCreating(true)}>New invoice</Button>} />
      <div className="stack">
        <Card><h2>Subscription</h2>{sub.data?.subscription ? <p><strong>{sub.data.subscription.plan}</strong> · {sub.data.subscription.price} per period · since {sub.data.subscription.starts_on}</p> : <p className="muted">No active subscription.</p>}</Card>
        {list.error && <Alert kind="error">{list.error}</Alert>}
        <DataTable rows={list.data?.items ?? []} rowKey={(i) => i.invoice_id} empty={<EmptyState title="No invoices" />}
          columns={[{ header: "Invoice", render: (i) => <button className="link-btn" onClick={() => setOpen(i.invoice_id)}>{i.invoice_number}</button> }, { header: "Amount", render: (i) => i.amount }, { header: "Paid", render: (i) => i.paid }, { header: "Balance", render: (i) => i.balance }, { header: "Due", render: (i) => i.due_on }, { header: "Status", render: (i) => <StatusBadge status={i.status} /> }]} />
        <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
      </div>
      {creating && (
        <Modal title="New invoice" onClose={() => setCreating(false)}>
          {error && <Alert kind="error">{error}</Alert>}
          <Field label="Amount (INR)" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
          <Field label="Due date" type="date" value={f.due_on} onChange={(e) => setF({ ...f, due_on: e.target.value })} />
          <Button busy={busy} disabled={!f.amount || !f.due_on} onClick={() => void run(async () => { await api(`/api/v1/providers/${providerId}/invoices`, { method: "POST", body: f }); setCreating(false); list.reload(); })}>Issue invoice</Button>
        </Modal>
      )}
      {open && <InvoiceDialog id={open} routine={can("billing.routine")} refunds={me?.primary_role === "SUPER_ADMIN"} onClose={() => { setOpen(null); list.reload(); }} />}
    </>
  );
}

function InvoiceDialog({ id, routine, refunds, onClose }: { id: string; routine: boolean; refunds: boolean; onClose: () => void }) {
  const d = useApi<InvDetail>(`/api/v1/invoices/${id}`);
  const [pay, setPay] = useState({ amount: "", method: "BANK_TRANSFER", external_reference: "" });
  const [adj, setAdj] = useState({ kind: "REFUND", amount: "", reason: "" });
  const act = useAction();
  return (
    <Modal title={d.data?.invoice_number ?? "Invoice"} onClose={onClose}>
      {d.loading && <Spinner />}{d.error && <Alert kind="error">{d.error}</Alert>}{act.error && <Alert kind="error">{act.error}</Alert>}
      {d.data && (
        <>
          <p><StatusBadge status={d.data.status} /> Amount {d.data.amount} · paid {d.data.paid} · refunded {d.data.refunded} · <strong>balance {d.data.balance}</strong></p>
          <DataTable rows={d.data.payments} rowKey={(p) => p.at} empty={<EmptyState title="No payments recorded" />} columns={[{ header: "Payment", render: (p) => p.amount }, { header: "Method", render: (p) => p.method }, { header: "Reference", render: (p) => p.reference }, { header: "When", render: (p) => when(p.at) }]} />
          {d.data.adjustments.length > 0 && <DataTable rows={d.data.adjustments} rowKey={(a) => a.at} columns={[{ header: "Adjustment", render: (a) => `${a.kind} ${a.amount}` }, { header: "Reason", render: (a) => a.reason }, { header: "When", render: (a) => when(a.at) }]} />}
          {routine && Number(d.data.balance) > 0 && (
            <div style={{ marginTop: "var(--space-4)" }}><h3>Record a payment</h3>
              <p className="muted small">Record only payments you have confirmed against a bank or receipt reference. No payment gateway is connected.</p>
              <div className="grid cols-2"><Field label="Amount" inputMode="decimal" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} />
                <SelectField label="Method" value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}>{["BANK_TRANSFER", "CHEQUE", "CASH", "UPI", "CARD", "OTHER"].map((m) => <option key={m}>{m}</option>)}</SelectField></div>
              <Field label="External reference" value={pay.external_reference} onChange={(e) => setPay({ ...pay, external_reference: e.target.value })} />
              <Button busy={act.busy} disabled={!pay.amount || pay.external_reference.length < 3} onClick={() => void act.run(async () => { await api(`/api/v1/invoices/${id}/payments`, { method: "POST", body: pay, headers: { "Idempotency-Key": key() } }); setPay({ ...pay, amount: "", external_reference: "" }); d.reload(); })}>Record payment</Button></div>
          )}
          {refunds && (
            <div style={{ marginTop: "var(--space-4)" }}><h3>Refund or adjustment (Super Admin)</h3>
              <div className="grid cols-2"><SelectField label="Type" value={adj.kind} onChange={(e) => setAdj({ ...adj, kind: e.target.value })}><option>REFUND</option><option>CREDIT</option><option>WRITE_OFF</option></SelectField><Field label="Amount" value={adj.amount} onChange={(e) => setAdj({ ...adj, amount: e.target.value })} /></div>
              <TextAreaField label="Reason (required, permanent)" value={adj.reason} onChange={(e) => setAdj({ ...adj, reason: e.target.value })} />
              <Button variant="danger" busy={act.busy} disabled={!adj.amount || adj.reason.length < 5} onClick={() => void act.run(async () => { await api(`/api/v1/invoices/${id}/adjustments`, { method: "POST", body: adj, headers: { "Idempotency-Key": key() } }); setAdj({ ...adj, amount: "", reason: "" }); d.reload(); })}>Apply</Button></div>
          )}
        </>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- settings
export function SettingsPage() {
  const { can } = useAuth();
  const { providerId, branchId } = useScope();
  const platform = useApi<Record<string, unknown>>(can("settings.platform") ? "/api/v1/settings/platform" : null);
  const provider = useApi<Record<string, unknown>>(providerId && can("settings.provider") ? `/api/v1/settings/providers/${providerId}` : null);
  const branch = useApi<Record<string, unknown>>(branchId && can("settings.branch") ? `/api/v1/settings/branches/${branchId}` : null);
  const sections = [
    { id: "platform", title: "Platform", enabled: can("settings.platform"), data: platform, path: "/api/v1/settings/platform" },
    { id: "provider", title: "Provider", enabled: can("settings.provider") && !!providerId, data: provider, path: `/api/v1/settings/providers/${providerId}` },
    { id: "branch", title: "Branch", enabled: can("settings.branch") && !!branchId, data: branch, path: `/api/v1/settings/branches/${branchId}` },
  ];
  return (
    <>
      <PageHeader title="Settings" subtitle="Changes are audited with the old and new value" />
      <div className="grid cols-2">{sections.filter((s) => s.enabled).map((s) => <SettingsCard key={s.id} title={s.title} path={s.path} data={s.data.data} reload={s.data.reload} error={s.data.error} />)}</div>
    </>
  );
}

function SettingsCard({ title, path, data, reload, error }: { title: string; path: string; data: Record<string, unknown> | null; reload: () => void; error: string }) {
  const [duration, setDuration] = useState("");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const act = useAction();
  const save = (k: string, v: unknown) => act.run(async () => { await api(path, { method: "PUT", body: { key: k, value: v } }); reload(); });
  return (
    <Card>
      <h2>{title}</h2>
      {(error || act.error) && <Alert kind="error">{error || act.error}</Alert>}
      <p className="small muted">Current: {data && Object.keys(data).length ? JSON.stringify(data) : "defaults"}</p>
      <div className="row"><Field label="Default exam duration (min)" type="number" value={duration} onChange={(e) => setDuration(e.target.value)} /><Button small busy={act.busy} disabled={!duration} onClick={() => void save("exam.default_duration_minutes", Number(duration))}>Save</Button></div>
      <div className="row"><SelectField label="Email notifications" value={email} onChange={(e) => setEmail(e.target.value)}><option value="">—</option><option value="true">Enabled</option><option value="false">Disabled</option></SelectField><Button small disabled={!email} onClick={() => void save("notifications.email_enabled", email === "true")}>Save</Button></div>
      <div className="row"><Field label="Display name" value={name} onChange={(e) => setName(e.target.value)} /><Button small disabled={!name} onClick={() => void save("branding.display_name", name)}>Save</Button></div>
    </Card>
  );
}

// ---------------------------------------------------------------- audit
type A = { audit_id: string; at: string; actor: string | null; action: string; target_type: string | null; target_id: string | null; outcome: string; reason: string | null };
export function AuditPage() {
  const [page, setPage] = useState(1);
  const [outcome, setOutcome] = useState("");
  const [action, setAction] = useState("");
  const list = useApi<Page<A>>(`/api/v1/audit-logs?page=${page}&page_size=50${outcome ? `&outcome=${outcome}` : ""}${action ? `&action=${encodeURIComponent(action)}` : ""}`);
  return (
    <>
      <PageHeader title="Audit log" subtitle="Read-only. Entries cannot be edited or deleted from the application." />
      <div className="row"><SelectField label="Outcome" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(1); }}><option value="">All</option><option>SUCCESS</option><option>DENIED</option><option>FAILURE</option></SelectField>
        <Field label="Action (exact)" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} placeholder="auth.login" /></div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading ? <Spinner /> : (
        <div className="stack">
          <DataTable rows={list.data?.items ?? []} rowKey={(a) => a.audit_id} empty={<EmptyState title="No matching entries" />}
            columns={[{ header: "When", render: (a) => when(a.at) }, { header: "Actor", render: (a) => a.actor?.slice(0, 8) ?? "system" }, { header: "Action", render: (a) => a.action }, { header: "Target", render: (a) => `${a.target_type ?? ""} ${a.target_id?.slice(0, 8) ?? ""}` }, { header: "Outcome", render: (a) => <StatusBadge status={a.outcome} /> }, { header: "Reason", render: (a) => a.reason ?? "" }]} />
          <Pagination page={page} pageSize={50} total={list.data?.total ?? 0} onChange={setPage} />
        </div>
      )}
    </>
  );
}
