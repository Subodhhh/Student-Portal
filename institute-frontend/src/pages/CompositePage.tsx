import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, DataTable, EmptyState, Field, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, Tabs, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

type Subject = { subject_id: string; name: string };
type Slot = { slot_id: string; subject_id: string; provider_id: string; status: string; paper_version_id: string | null; responded_at: string | null; decline_reason: string | null };
type Req = { request_id: string; name: string; note: string | null; status: string; created_at: string; respond_by: string | null; initiator_provider_id: string; slots: Slot[]; missing: { slot_id: string }[] };
type Invitation = { request: { request_id: string; name: string; note: string | null; status: string; created_at: string; initiator_provider_name: string | null }; slot: Slot };
type ProviderHit = { provider_id: string; name: string };
type PaperRow = { paper_id: string; title: string; latest_version: { paper_version_id: string; version_no: number; status: string; question_count: number } | null };

const fmt = (s: string) => new Date(s).toLocaleString();

export default function CompositePage() {
  const { can } = useAuth();
  const canRequest = can("examplan.manage");
  const [chosen, setChosen] = useState("");
  const tab = chosen || (canRequest ? "requests" : "invitations");     // derived, so it is right even before the session has loaded
  const subjects = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  const subjectName = (id: string) => subjects.data?.items.find((s) => s.subject_id === id)?.name ?? "Subject";
  return (
    <>
      <PageHeader title="Composite tests" subtitle="Combine approved papers from several providers into one exam. Papers are shared for use only: nobody sees another provider's questions." />
      <Tabs active={tab} onChange={setChosen} tabs={[...(canRequest ? [{ id: "requests", label: "My requests" }] : []), { id: "invitations", label: "Invitations to contribute" }]} />
      {tab === "requests" && canRequest && <NeedBranch>{({ providerId, branchId }) => <Requests providerId={providerId} branchId={branchId} subjectName={subjectName} subjects={subjects.data?.items ?? []} />}</NeedBranch>}
      {tab === "invitations" && <Invitations subjectName={subjectName} />}
    </>
  );
}

function Requests({ providerId, branchId, subjects, subjectName }: { providerId: string; branchId: string; subjects: Subject[]; subjectName: (id: string) => string }) {
  const scope = useScope();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const list = useApi<Page<Req>>(`/api/v1/branches/${branchId}/composite-requests?page=${page}&page_size=10${status ? `&status=${status}` : ""}`);
  const [creating, setCreating] = useState(false);
  const [cancelling, setCancelling] = useState<Req | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const providerName = (id: string) => (id === providerId ? scope.provider?.name ?? "This provider" : names[id] ?? "Invited provider");
  return (
    <div className="stack">
      <Card>
        <div className="row between">
          <h2>Requests from {scope.branch?.name ?? "this branch"}</h2>
          <div className="row">
            <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All</option><option>OPEN</option><option>COMPLETE</option><option>CANCELLED</option><option>EXPIRED</option>
            </SelectField>
            <Button onClick={() => setCreating(true)}>New request</Button>
          </div>
        </div>
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(r) => r.request_id}
              empty={<EmptyState title="No composite requests" hint="Start one to ask other providers for approved papers." />}
              columns={[
                { header: "Request", render: (r) => <><strong>{r.name}</strong><div className="small muted">{fmt(r.created_at)}</div></> },
                { header: "Subjects and contributors", render: (r) => (
                  <ul className="plain">{r.slots.map((s) => <li key={s.slot_id}>{subjectName(s.subject_id)} · {providerName(s.provider_id)} <StatusBadge status={s.status} />{s.decline_reason ? <span className="small muted"> — {s.decline_reason}</span> : null}</li>)}</ul>) },
                { header: "Status", render: (r) => <><StatusBadge status={r.status} />{r.respond_by && r.status === "OPEN" ? <div className="small muted">Respond by {new Date(r.respond_by).toLocaleString()}</div> : null}</> },
                { header: "", render: (r) => (<div className="row">
                  {r.status !== "CANCELLED" && r.slots.some((s) => s.status === "CONTRIBUTED") && <Link to="/exam-plans">Build exam plan</Link>}
                  {r.status === "OPEN" && <Button variant="secondary" small onClick={() => setCancelling(r)}>Cancel</Button>}</div>) },
              ]} />
            <Pagination page={page} pageSize={10} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
        <p className="small muted">When contributions arrive, create the exam plan and choose each contributed paper version for its subject. Subjects whose paper never arrives are left out when the exam is generated, and the exam page lists them.</p>
      </Card>
      {creating && <NewRequest providerId={providerId} branchId={branchId} subjects={subjects} onClose={() => setCreating(false)}
        onCreated={(known) => { setNames((n) => ({ ...n, ...known })); setCreating(false); list.reload(); }} />}
      {cancelling && <CancelRequest req={cancelling} onClose={() => setCancelling(null)} onDone={() => { setCancelling(null); list.reload(); }} />}
    </div>
  );
}

function NewRequest({ providerId, branchId, subjects, onClose, onCreated }: { providerId: string; branchId: string; subjects: Subject[]; onClose: () => void; onCreated: (names: Record<string, string>) => void }) {
  const scope = useScope();
  const act = useAction();
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [respondBy, setRespondBy] = useState("");   // local date-time from the input; sent as an absolute instant
  const [rows, setRows] = useState<{ subject_id: string; provider_id: string }[]>([{ subject_id: "", provider_id: providerId }]);
  const [q, setQ] = useState("");
  const [known, setKnown] = useState<Record<string, string>>(scope.provider ? { [providerId]: scope.provider.name } : {});
  const [key] = useState(() => crypto.randomUUID());     // one key per form: a retried submit cannot create a second request
  const hits = useApi<ProviderHit[]>(q.trim().length >= 2 ? `/api/v1/composite-providers?q=${encodeURIComponent(q.trim())}` : null);
  const options = useMemo(() => {
    const merged = { ...known };
    (hits.data ?? []).forEach((h) => { merged[h.provider_id] = h.name; });
    return merged;
  }, [known, hits.data]);
  const setRow = (i: number, patch: Partial<{ subject_id: string; provider_id: string }>) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const valid = name.trim().length >= 3 && rows.length > 0 && rows.every((r) => r.subject_id && r.provider_id) && new Set(rows.map((r) => r.subject_id)).size === rows.length;
  const submit = () => act.run(async () => {
    await api(`/api/v1/branches/${branchId}/composite-requests`, { method: "POST", body: { name: name.trim(), note: note.trim() || null, slots: rows, respond_by: respondBy ? new Date(respondBy).toISOString() : null }, headers: { "Idempotency-Key": key } });
    onCreated(options);
  });
  return (
    <Modal title="New composite test request" onClose={onClose}>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} hint="For example: Joint NEET mock, May" />
      <TextAreaField label="Note for the invited providers (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Field label="Respond by (optional)" type="datetime-local" value={respondBy} onChange={(e) => setRespondBy(e.target.value)}
        hint="Invitations still unanswered at this time expire; papers already contributed stay usable. At least 1 hour ahead." />
      <Field label="Find providers to invite" value={q} onChange={(e) => setQ(e.target.value)} hint="Type at least 2 letters of the provider's name; you can also choose your own provider for a subject." />
      {hits.loading && q.trim().length >= 2 && <Spinner />}
      {rows.map((r, i) => (
        <div className="grid cols-2" key={i}>
          <SelectField label={`Subject ${i + 1}`} value={r.subject_id} onChange={(e) => setRow(i, { subject_id: e.target.value })}>
            <option value="">Select…</option>
            {subjects.filter((s) => s.subject_id === r.subject_id || !rows.some((x) => x.subject_id === s.subject_id)).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
          </SelectField>
          <SelectField label="Supplied by" value={r.provider_id} onChange={(e) => setRow(i, { provider_id: e.target.value })}>
            {Object.entries(options).map(([id, n]) => <option key={id} value={id}>{id === providerId ? `${n} (this provider)` : n}</option>)}
          </SelectField>
        </div>
      ))}
      <div className="row">
        <Button variant="secondary" small onClick={() => { setKnown(options); setRows((rs) => [...rs, { subject_id: "", provider_id: providerId }]); }} disabled={rows.length >= Math.max(subjects.length, 1)}>Add a subject</Button>
        {rows.length > 1 && <Button variant="secondary" small onClick={() => setRows((rs) => rs.slice(0, -1))}>Remove last</Button>}
      </div>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={act.busy} disabled={!valid} onClick={() => void submit()}>Send request</Button>
      </div>
    </Modal>
  );
}

function CancelRequest({ req, onClose, onDone }: { req: Req; onClose: () => void; onDone: () => void }) {
  const act = useAction();
  const [reason, setReason] = useState("");
  return (
    <Modal title={`Cancel "${req.name}"`} onClose={onClose}>
      <p>Invited providers who have not answered are told the request is cancelled. Papers already shared stay available until the owner withdraws the share.</p>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button variant="secondary" onClick={onClose}>Keep request</Button>
        <Button variant="danger" busy={act.busy} disabled={reason.trim().length < 5} onClick={() => void act.run(async () => { await api(`/api/v1/composite-requests/${req.request_id}/cancel`, { method: "POST", body: { reason } }); onDone(); })}>Cancel request</Button>
      </div>
    </Modal>
  );
}

function Invitations({ subjectName }: { subjectName: (id: string) => string }) {
  const list = useApi<Invitation[]>("/api/v1/composite-invitations");
  const [responding, setResponding] = useState<{ inv: Invitation; mode: "contribute" | "decline" } | null>(null);
  return (
    <Card>
      <h2>Invitations to contribute a paper</h2>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading ? <Spinner /> : (
        <DataTable rows={list.data ?? []} rowKey={(i) => i.slot.slot_id}
          empty={<EmptyState title="No invitations" hint="When another provider asks for one of your papers, it appears here." />}
          columns={[
            { header: "Composite test", render: (i) => <><strong>{i.request.name}</strong><div className="small muted">from {i.request.initiator_provider_name ?? "another provider"} · {fmt(i.request.created_at)}</div>{i.request.note && <div className="small">{i.request.note}</div>}</> },
            { header: "Subject", render: (i) => subjectName(i.slot.subject_id) },
            { header: "Status", render: (i) => <StatusBadge status={i.request.status === "CANCELLED" ? "CANCELLED" : i.slot.status} /> },
            { header: "", render: (i) => i.slot.status === "PENDING" && i.request.status === "OPEN" ? (
              <div className="row"><Button small onClick={() => setResponding({ inv: i, mode: "contribute" })}>Contribute a paper</Button>
                <Button variant="secondary" small onClick={() => setResponding({ inv: i, mode: "decline" })}>Decline</Button></div>) : null },
          ]} />
      )}
      {responding?.mode === "contribute" && <Contribute inv={responding.inv} onClose={() => setResponding(null)} onDone={() => { setResponding(null); list.reload(); }} />}
      {responding?.mode === "decline" && <Decline inv={responding.inv} onClose={() => setResponding(null)} onDone={() => { setResponding(null); list.reload(); }} />}
    </Card>
  );
}

function Contribute({ inv, onClose, onDone }: { inv: Invitation; onClose: () => void; onDone: () => void }) {
  const scope = useScope();
  const act = useAction();
  const [versionId, setVersionId] = useState("");
  const wrongProvider = scope.providerId !== inv.slot.provider_id;
  const papers = useApi<Page<PaperRow>>(!wrongProvider && scope.branchId ? `/api/v1/branches/${scope.branchId}/papers?subject_id=${inv.slot.subject_id}&status=PUBLISHED&page_size=100` : null);
  const published = (papers.data?.items ?? []).filter((p) => p.latest_version?.status === "PUBLISHED");
  return (
    <Modal title={`Contribute a paper to "${inv.request.name}"`} onClose={onClose}>
      <p>Only an approved, <strong>published</strong> paper for this subject can be contributed. The requesting provider may then use it in an exam but cannot read its questions.</p>
      {wrongProvider && <Alert kind="warn">Switch the provider in the top bar to the invited provider before choosing a paper.</Alert>}
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {papers.loading && <Spinner />}
      {!wrongProvider && !papers.loading && published.length === 0 && <Alert kind="info">The selected branch has no published paper for this subject. Choose another branch in the top bar, or publish one first.</Alert>}
      <SelectField label="Published paper" value={versionId} onChange={(e) => setVersionId(e.target.value)}>
        <option value="">Select…</option>
        {published.map((p) => <option key={p.paper_id} value={p.latest_version!.paper_version_id}>{p.title} — v{p.latest_version!.version_no}, {p.latest_version!.question_count} questions</option>)}
      </SelectField>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={act.busy} disabled={!versionId || wrongProvider} onClick={() => void act.run(async () => { await api(`/api/v1/composite-requests/${inv.request.request_id}/slots/${inv.slot.slot_id}/contribute`, { method: "POST", body: { paper_version_id: versionId } }); onDone(); })}>Contribute and share for use</Button>
      </div>
    </Modal>
  );
}

function Decline({ inv, onClose, onDone }: { inv: Invitation; onClose: () => void; onDone: () => void }) {
  const act = useAction();
  const [reason, setReason] = useState("");
  return (
    <Modal title={`Decline "${inv.request.name}"`} onClose={onClose}>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button variant="secondary" onClick={onClose}>Back</Button>
        <Button variant="danger" busy={act.busy} disabled={reason.trim().length < 5} onClick={() => void act.run(async () => { await api(`/api/v1/composite-requests/${inv.request.request_id}/slots/${inv.slot.slot_id}/decline`, { method: "POST", body: { reason } }); onDone(); })}>Decline</Button>
      </div>
    </Modal>
  );
}
