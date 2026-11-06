import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { AuditTable } from "../components/audit";
import ScopeBar from "../components/ScopeBar";
import { Alert, Button, Card, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

const when = (s: string) => new Date(s).toLocaleString();

/* ---------- Icons ---------- */
const ICONS = {
  bell: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></svg>,
  support: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><line x1="12" x2="12.01" y1="17" y2="17" /></svg>,
  billing: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="20" height="14" x="2" y="5" rx="2" /><line x1="2" x2="22" y1="10" y2="10" /></svg>,
  settings: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" /><circle cx="12" cy="12" r="3" /></svg>,
  audit: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" x2="8" y1="13" y2="13" /><line x1="16" x2="8" y1="17" y2="17" /><polyline points="10 9 9 9 8 9" /></svg>,
};

/* =========================================================
   Notifications
   ========================================================= */
type N = { notification_id: string; kind: string; title: string; body: string; created_at: string; read_at: string | null };

export function NotificationsPage() {
  const [page, setPage] = useState(1);
  const list = useApi<Page<N>>(`/api/v1/notifications?page=${page}&page_size=20`);
  const act = useAction();
  return (
    <>
      <PageHeader 
        subtitle="Messages about your exams, papers, enrollments and support tickets" 
        actions={
          <Button variant="secondary" busy={act.busy} onClick={() => void act.run(async () => { await api("/api/v1/notifications/read-all", { method: "POST" }); list.reload(); })}>
            Mark all read
          </Button>
        } 
      />
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading ? <Spinner /> : (
        <div className="stack">
          {(list.data?.items ?? []).length === 0 && <Card><EmptyState title="You're all caught up" /></Card>}
          {(list.data?.items ?? []).map((n) => (
            <Card key={n.notification_id} hover>
              <div className="row between" style={{ marginBottom: "8px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span className="card-section-badge" style={{ width: 28, height: 28 }}>{ICONS.bell}</span>
                  <strong>{n.title}</strong>
                </div>
                <span className="muted small">{when(n.created_at)}</span>
              </div>
              <p style={{ whiteSpace: "pre-wrap", margin: "0 0 12px 0" }}>{n.body}</p>
              <div className="row" style={{ alignItems: "center" }}>
                <StatusBadge status={n.read_at ? "SENT" : "PENDING"} />
                {!n.read_at && (
                  <Button small variant="secondary" onClick={() => void act.run(async () => { await api(`/api/v1/notifications/${n.notification_id}/read`, { method: "POST" }); list.reload(); })}>
                    Mark read
                  </Button>
                )}
              </div>
            </Card>
          ))}
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </div>
      )}
    </>
  );
}

/* =========================================================
   Support
   ========================================================= */
type T = { ticket_id: string; subject: string; status: string; priority: string; created_at: string };
type TD = { ticket: T; messages: { author: string; body: string; at: string }[] };

export function SupportPage() {
  const { can, me } = useAuth();
  const scope = useScope();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const list = useApi<Page<T>>(`/api/v1/support/tickets?page=${page}&page_size=20${status ? `&status=${status}` : ""}`);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const isGlobal = me?.primary_role === "SUPER_ADMIN" || me?.primary_role === "ADMIN";
  const [f, setF] = useState({ subject: "", body: "", priority: "NORMAL" });
  const [providerId, setProviderId] = useState("");
  const ticketProvider = providerId || (isGlobal ? "" : scope.providerId);
  const { busy, error, run } = useAction();

  return (
    <>
      <PageHeader 
        subtitle="Tickets you raised, or that your scope can manage" 
        actions={<Button onClick={() => setCreating(true)}>+ New ticket</Button>} 
      />
      <div style={{ marginBottom: "1rem" }}>
      <Card>
        <div className="toolbar" style={{ marginBottom: 0, paddingBottom: 0, borderBottom: "none" }}>
          <SelectField label="Filter by Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All tickets</option>
            <option value="OPEN">Open</option>
            <option value="IN_PROGRESS">In progress</option>
            <option value="RESOLVED">Resolved</option>
            <option value="CLOSED">Closed</option>
          </SelectField>
        </div>
      </Card>
    </div>

      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      
      {list.loading ? <Spinner /> : (
        <Card className="p-0 overflow-hidden">
          <DataTable 
            rows={list.data?.items ?? []} 
            rowKey={(t) => t.ticket_id} 
            empty={<EmptyState title="No tickets" />}
            columns={[
              { header: "Subject", render: (t) => <button className="link-btn" style={{ background: "none", border: "none", color: "var(--c-primary)", cursor: "pointer", fontWeight: 500, padding: 0 }} onClick={() => setOpen(t.ticket_id)}>{t.subject}</button> }, 
              { header: "Priority", render: (t) => <span className="badge info">{t.priority}</span> }, 
              { header: "Status", render: (t) => <StatusBadge status={t.status} /> }, 
              { header: "Opened", render: (t) => <span className="muted small">{when(t.created_at)}</span> }
            ]} 
          />
          <div style={{ padding: "1rem", borderTop: "1px solid var(--c-border)" }}>
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </div>
        </Card>
      )}

      {creating && (
        <Modal title="New support ticket" onClose={() => setCreating(false)}>
          {error && <Alert kind="error">{error}</Alert>}
          <Field label="Subject" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} />
          <SelectField label="Priority" value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>
            <option>LOW</option>
            <option>NORMAL</option>
            <option>HIGH</option>
          </SelectField>
          {scope.providers.length > 0 && (
            <SelectField label="Related provider" value={ticketProvider} onChange={(e) => setProviderId(e.target.value)}>
              {isGlobal && <option value="">None (platform)</option>}
              {scope.providers.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
            </SelectField>
          )}
          <TextAreaField label="Describe the problem" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} />
          <div className="modal-actions">
            <Button variant="secondary" onClick={() => setCreating(false)}>Cancel</Button>
            <Button busy={busy} disabled={f.subject.length < 3 || !f.body} onClick={() => void run(async () => { await api("/api/v1/support/tickets", { method: "POST", body: { ...f, provider_id: ticketProvider || null } }); setCreating(false); list.reload(); })}>Submit</Button>
          </div>
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
      {d.loading && <Spinner />}
      {d.error && <Alert kind="error">{d.error}</Alert>}
      {act.error && <Alert kind="error">{act.error}</Alert>}
      
      {d.data && (
        <>
          <div style={{ marginBottom: "1rem" }}><StatusBadge status={d.data.ticket.status} /></div>
          <div className="stack" style={{ marginBottom: "1.5rem" }}>
            {d.data.messages.map((m, i) => (
              <div key={i} className="card" style={{ background: "var(--c-surface-hi)", padding: "1rem" }}>
                <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{m.body}</p>
                <span className="muted small" style={{ display: "block", marginTop: "8px" }}>{when(m.at)}</span>
              </div>
            ))}
          </div>
          
          {d.data.ticket.status !== "CLOSED" && (
            <>
              <TextAreaField label="Reply" value={msg} onChange={(e) => setMsg(e.target.value)} />
              <div className="modal-actions" style={{ justifyContent: "flex-start", gap: "1rem" }}>
                <Button busy={act.busy} disabled={!msg} onClick={() => void act.run(async () => { await api(`/api/v1/support/tickets/${id}/messages`, { method: "POST", body: { body: msg } }); setMsg(""); d.reload(); })}>Send Reply</Button>
                <div style={{ flex: 1 }} />
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

/* =========================================================
   Billing
   ========================================================= */
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
      <PageHeader 
        subtitle="Subscription, invoices and payment history of a provider" 
        actions={can("billing.routine") && providerId && <Button onClick={() => setCreating(true)}>+ New invoice</Button>} 
      />
      
      <div style={{ marginBottom: "1rem" }}><ScopeBar branch={false} /></div>
      
      <div className="stack">
        <Card>
          <div className="card-section-title">
            <span className="card-section-badge">{ICONS.billing}</span>
            <h2>Subscription Status</h2>
          </div>
          {sub.data?.subscription ? (
            <p style={{ fontSize: "1.1rem", marginTop: "1rem" }}>
              <strong>{sub.data.subscription.plan}</strong> · {sub.data.subscription.price} per period · since {sub.data.subscription.starts_on}
            </p>
          ) : (
            <p className="muted" style={{ marginTop: "1rem" }}>No active subscription.</p>
          )}
        </Card>

        {list.error && <Alert kind="error">{list.error}</Alert>}
        
        <Card className="p-0 overflow-hidden">
          <DataTable 
            rows={list.data?.items ?? []} 
            rowKey={(i) => i.invoice_id} 
            empty={<EmptyState title="No invoices" />}
            columns={[
              { header: "Invoice", render: (i) => <button className="link-btn" style={{ background: "none", border: "none", color: "var(--c-primary)", cursor: "pointer", fontWeight: 500, padding: 0 }} onClick={() => setOpen(i.invoice_id)}>{i.invoice_number}</button> }, 
              { header: "Amount", render: (i) => i.amount }, 
              { header: "Paid", render: (i) => <span className="muted">{i.paid}</span> }, 
              { header: "Balance", render: (i) => <strong style={{ color: Number(i.balance) > 0 ? "var(--c-danger)" : "var(--c-ok)" }}>{i.balance}</strong> }, 
              { header: "Due", render: (i) => <span className="muted small">{i.due_on}</span> }, 
              { header: "Status", render: (i) => <StatusBadge status={i.status} /> }
            ]} 
          />
          <div style={{ padding: "1rem", borderTop: "1px solid var(--c-border)" }}>
            <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
          </div>
        </Card>
      </div>

      {creating && (
        <Modal title="New invoice" onClose={() => setCreating(false)}>
          {error && <Alert kind="error">{error}</Alert>}
          <Field label="Amount (INR)" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
          <Field label="Due date" type="date" value={f.due_on} onChange={(e) => setF({ ...f, due_on: e.target.value })} />
          <div className="modal-actions">
            <Button variant="secondary" onClick={() => setCreating(false)}>Cancel</Button>
            <Button busy={busy} disabled={!f.amount || !f.due_on} onClick={() => void run(async () => { await api(`/api/v1/providers/${providerId}/invoices`, { method: "POST", body: f }); setCreating(false); list.reload(); })}>Issue invoice</Button>
          </div>
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
      {d.loading && <Spinner />}
      {d.error && <Alert kind="error">{d.error}</Alert>}
      {act.error && <Alert kind="error">{act.error}</Alert>}
      
      {d.data && (
        <>
          <div style={{ display: "flex", gap: "1rem", alignItems: "center", marginBottom: "1.5rem", padding: "1rem", background: "var(--c-surface-hi)", borderRadius: "var(--radius-md)" }}>
            <StatusBadge status={d.data.status} />
            <span className="small">Amount {d.data.amount} · paid {d.data.paid} · refunded {d.data.refunded}</span>
            <div style={{ flex: 1 }} />
            <strong style={{ fontSize: "1.2rem", color: Number(d.data.balance) > 0 ? "var(--c-danger)" : "var(--c-ok)" }}>Balance: {d.data.balance}</strong>
          </div>

          <h3 style={{ fontSize: "1rem" }}>Payments</h3>
          <DataTable 
            rows={d.data.payments} 
            rowKey={(p) => p.at} 
            empty={<EmptyState title="No payments recorded" />} 
            columns={[
              { header: "Payment", render: (p) => p.amount }, 
              { header: "Method", render: (p) => p.method }, 
              { header: "Reference", render: (p) => <span className="muted">{p.reference}</span> }, 
              { header: "When", render: (p) => <span className="small muted">{when(p.at)}</span> }
            ]} 
          />
          
          {d.data.adjustments.length > 0 && (
            <>
              <h3 style={{ fontSize: "1rem", marginTop: "1.5rem" }}>Adjustments</h3>
              <DataTable rows={d.data.adjustments} rowKey={(a) => a.at} columns={[
                { header: "Adjustment", render: (a) => `${a.kind} ${a.amount}` }, 
                { header: "Reason", render: (a) => a.reason }, 
                { header: "When", render: (a) => <span className="small muted">{when(a.at)}</span> }
              ]} />
            </>
          )}

          {routine && Number(d.data.balance) > 0 && (
            <div style={{ marginTop: "2rem", paddingTop: "1.5rem", borderTop: "1px solid var(--c-border)" }}>
              <h3 style={{ fontSize: "1rem", marginBottom: "0.25rem" }}>Record a payment</h3>
              <p className="muted small" style={{ marginBottom: "1rem" }}>Record only payments you have confirmed against a bank or receipt reference.</p>
              
              <div className="grid cols-2">
                <Field label="Amount" inputMode="decimal" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} />
                <SelectField label="Method" value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}>
                  {["BANK_TRANSFER", "CHEQUE", "CASH", "UPI", "CARD", "OTHER"].map((m) => <option key={m}>{m}</option>)}
                </SelectField>
              </div>
              
              <Field label="External reference" value={pay.external_reference} onChange={(e) => setPay({ ...pay, external_reference: e.target.value })} />
              
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "1rem" }}>
                <Button busy={act.busy} disabled={!pay.amount || pay.external_reference.length < 3} onClick={() => void act.run(async () => { await api(`/api/v1/invoices/${id}/payments`, { method: "POST", body: pay, headers: { "Idempotency-Key": key() } }); setPay({ ...pay, amount: "", external_reference: "" }); d.reload(); })}>Record payment</Button>
              </div>
            </div>
          )}

          {refunds && (
            <div style={{ marginTop: "2rem", paddingTop: "1.5rem", borderTop: "1px solid var(--c-border)" }}>
              <h3 style={{ fontSize: "1rem", marginBottom: "0.25rem", color: "var(--c-danger)" }}>Refund or adjustment (Super Admin)</h3>
              
              <div className="grid cols-2">
                <SelectField label="Type" value={adj.kind} onChange={(e) => setAdj({ ...adj, kind: e.target.value })}>
                  <option>REFUND</option><option>CREDIT</option><option>WRITE_OFF</option>
                </SelectField>
                <Field label="Amount" value={adj.amount} onChange={(e) => setAdj({ ...adj, amount: e.target.value })} />
              </div>
              
              <TextAreaField label="Reason (required, permanent)" value={adj.reason} onChange={(e) => setAdj({ ...adj, reason: e.target.value })} />
              
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "1rem" }}>
                <Button variant="danger" busy={act.busy} disabled={!adj.amount || adj.reason.length < 5} onClick={() => void act.run(async () => { await api(`/api/v1/invoices/${id}/adjustments`, { method: "POST", body: adj, headers: { "Idempotency-Key": key() } }); setAdj({ ...adj, amount: "", reason: "" }); d.reload(); })}>Apply Adjustment</Button>
              </div>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

/* =========================================================
   Settings
   ========================================================= */
export function SettingsPage() {
  const { can } = useAuth();
  const { providerId, branchId } = useScope();
  const platform = useApi<Record<string, unknown>>(can("settings.platform") ? "/api/v1/settings/platform" : null);
  const provider = useApi<Record<string, unknown>>(providerId && can("settings.provider") ? `/api/v1/settings/providers/${providerId}` : null);
  const branch = useApi<Record<string, unknown>>(branchId && can("settings.branch") ? `/api/v1/settings/branches/${branchId}` : null);
  
  const sections = [
    { id: "platform", title: "Platform Settings", enabled: can("settings.platform"), data: platform, path: "/api/v1/settings/platform" },
    { id: "provider", title: "Provider Settings", enabled: can("settings.provider") && !!providerId, data: provider, path: `/api/v1/settings/providers/${providerId}` },
    { id: "branch", title: "Branch Settings", enabled: can("settings.branch") && !!branchId, data: branch, path: `/api/v1/settings/branches/${branchId}` },
  ];
  const scoped = sections.filter((s) => s.id !== "platform" && (s.id === "provider" ? can("settings.provider") : can("settings.branch")));
  
  const card = (s: (typeof sections)[number]) => <SettingsCard key={s.id} title={s.title} path={s.path} data={s.data.data} reload={s.data.reload} error={s.data.error} />;
  
  return (
    <>
      <PageHeader subtitle="Changes are audited with the old and new value" />
      <div className="stack">
        {sections[0].enabled && <div className="grid cols-2">{card(sections[0])}</div>}
        {scoped.length > 0 && (
          <section className="stack" aria-label="Provider and branch settings" style={{ marginTop: "1rem" }}>
            {sections[0].enabled && <h2 style={{ fontSize: "1.2rem", marginBottom: "0.5rem" }}>Provider & Branch Settings</h2>}
            <div style={{ marginBottom: "1rem" }}><ScopeBar branch={can("settings.branch")} /></div>
            <div className="grid cols-2">{sections.filter((s) => s.id !== "platform" && s.enabled).map(card)}</div>
          </section>
        )}
      </div>
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
      <div className="card-section-title">
        <span className="card-section-badge">{ICONS.settings}</span>
        <h2>{title}</h2>
      </div>
      {(error || act.error) && <Alert kind="error">{error || act.error}</Alert>}
      
      <div style={{ padding: "1rem", background: "var(--c-surface-hi)", borderRadius: "var(--radius-md)", marginBottom: "1.5rem" }}>
        <p className="small muted" style={{ margin: 0 }}>
          <strong>Current Configuration:</strong> {data && Object.keys(data).length ? JSON.stringify(data, null, 2) : "Using system defaults."}
        </p>
      </div>

      <div className="stack">
        <div className="row" style={{ alignItems: "flex-end", gap: "1rem" }}>
          <div style={{ flex: 1 }}>
            <Field label="Default exam duration (min)" type="number" value={duration} onChange={(e) => setDuration(e.target.value)} />
          </div>
          <Button small busy={act.busy} disabled={!duration} onClick={() => void save("exam.default_duration_minutes", Number(duration))}>Save</Button>
        </div>
        
        <div className="row" style={{ alignItems: "flex-end", gap: "1rem" }}>
          <div style={{ flex: 1 }}>
            <SelectField label="Email notifications" value={email} onChange={(e) => setEmail(e.target.value)}>
              <option value="">— Select option —</option>
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </SelectField>
          </div>
          <Button small disabled={!email} onClick={() => void save("notifications.email_enabled", email === "true")}>Save</Button>
        </div>
        
        <div className="row" style={{ alignItems: "flex-end", gap: "1rem" }}>
          <div style={{ flex: 1 }}>
            <Field label="Display name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Button small disabled={!name} onClick={() => void save("branding.display_name", name)}>Save</Button>
        </div>
      </div>
    </Card>
  );
}

/* =========================================================
   Audit
   ========================================================= */
export function AuditPage() {
  const { me } = useAuth();
  const scope = useScope();
  const [f, setF] = useState({ from: "", to: "", actor_code: "", action: "", outcome: "", provider_id: "", target_type: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  
  const filters: Record<string, string> = {
    action: f.action.trim(), outcome: f.outcome, provider_id: f.provider_id, target_type: f.target_type, actor_code: f.actor_code.trim(),
    since: f.from ? new Date(`${f.from}T00:00`).toISOString() : "",
    until: f.to ? new Date(new Date(`${f.to}T00:00`).getTime() + 86400000).toISOString() : "",
  };
  const isGlobal = me?.primary_role === "SUPER_ADMIN" || me?.primary_role === "ADMIN";

  return (
    <>
      <PageHeader subtitle="Read-only. Entries cannot be edited or deleted from the application." />
      <div style={{ marginBottom: "1rem" }}>
      <Card>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "1rem", alignItems: "end" }}>
          <Field label="From" type="date" value={f.from} onChange={set("from")} />
          <Field label="To" type="date" value={f.to} onChange={set("to")} />
          <Field label="Actor (Staff ID)" value={f.actor_code} onChange={set("actor_code")} placeholder="STF000001" />
          <Field label="Action (exact)" value={f.action} onChange={set("action")} placeholder="staff.deactivate" />
          <SelectField label="Outcome" value={f.outcome} onChange={set("outcome")}>
            <option value="">All Outcomes</option>
            <option>SUCCESS</option>
            <option>DENIED</option>
            <option>FAILURE</option>
          </SelectField>
          <SelectField label="Record type" value={f.target_type} onChange={set("target_type")}>
            <option value="">All Types</option>
            {["staff", "provider", "branch", "subject", "question", "paper_version", "exam_plan", "exam", "global_test", "ticket", "invoice"].map((t) => <option key={t} value={t}>{t}</option>)}
          </SelectField>
          {(isGlobal || scope.providers.length > 1) && (
            <SelectField label="Provider" value={f.provider_id} onChange={set("provider_id")}>
              <option value="">All Providers</option>
              {scope.providers.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
            </SelectField>
          )}
        </div>
      </Card>
    </div>

      <Card className="p-0 overflow-hidden">
        <AuditTable key={JSON.stringify(filters)} filters={filters} pageSize={50} />
      </Card>
    </>
  );
}