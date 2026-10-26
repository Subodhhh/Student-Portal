import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import NeedBranch from "../components/NeedBranch";
import { Alert, Button, Card, DataTable, EmptyState, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, Tabs, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";

type Subject = { subject_id: string; name: string };
type Slot = { slot_id: string; subject_id: string; provider_id: string; status: string; paper_version_id: string | null; responded_at: string | null; decline_reason: string | null };
type Req = { request_id: string; name: string; note: string | null; status: string; created_at: string; respond_by: string | null; initiator_provider_id: string; slots: Slot[]; missing: { slot_id: string }[] };
type Invitation = { request: { request_id: string; name: string; note: string | null; status: string; created_at: string; initiator_provider_name: string | null }; slot: Slot };
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
      <PageHeader subtitle="History of composite requests. Papers were shared for use only: nobody sees another provider's questions." />
      <Alert kind="info">Composite Tests are retired: tests are now Daily, Weekly or Global. To offer a paper for a platform test, use <Link to="/tests/global">Global Tests</Link>.
        Requests made earlier stay here: open ones can still be answered or cancelled.</Alert>
      <Tabs active={tab} onChange={setChosen} tabs={[...(canRequest ? [{ id: "requests", label: "My requests" }] : []), { id: "invitations", label: "Invitations to contribute" }]} />
      {tab === "requests" && canRequest && <NeedBranch>{({ providerId, branchId }) => <Requests providerId={providerId} branchId={branchId} subjectName={subjectName} />}</NeedBranch>}
      {tab === "invitations" && <Invitations subjectName={subjectName} />}
    </>
  );
}

function Requests({ providerId, branchId, subjectName }: { providerId: string; branchId: string; subjectName: (id: string) => string }) {
  const scope = useScope();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const list = useApi<Page<Req>>(`/api/v1/branches/${branchId}/composite-requests?page=${page}&page_size=10${status ? `&status=${status}` : ""}`);
  const [cancelling, setCancelling] = useState<Req | null>(null);
  const providerName = (id: string) => (id === providerId ? scope.provider?.name ?? "This provider" : "Invited provider");
  return (
    <div className="stack">
      <Card>
        <div className="row between">
          <h2>Requests from {scope.branch?.name ?? "this branch"}</h2>
          <div className="row">
            <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All</option><option>OPEN</option><option>COMPLETE</option><option>CANCELLED</option><option>EXPIRED</option>
            </SelectField>
          </div>
        </div>
        {list.error && <Alert kind="error">{list.error}</Alert>}
        {list.loading ? <Spinner /> : (
          <>
            <DataTable rows={list.data?.items ?? []} rowKey={(r) => r.request_id}
              empty={<EmptyState title="No composite requests" />}
              columns={[
                { header: "Request", render: (r) => <><strong>{r.name}</strong><div className="small muted">{fmt(r.created_at)}</div></> },
                { header: "Subjects and contributors", render: (r) => (
                  <ul className="plain">{r.slots.map((s) => <li key={s.slot_id}>{subjectName(s.subject_id)} · {providerName(s.provider_id)} <StatusBadge status={s.status} />{s.decline_reason ? <span className="small muted"> — {s.decline_reason}</span> : null}</li>)}</ul>) },
                { header: "Status", render: (r) => <><StatusBadge status={r.status} />{r.respond_by && r.status === "OPEN" ? <div className="small muted">Respond by {new Date(r.respond_by).toLocaleString()}</div> : null}</> },
                { header: "", render: (r) => (<div className="row">
                  {r.status !== "CANCELLED" && r.slots.some((s) => s.status === "CONTRIBUTED") && <Link to="/exam-plans/new">Build a test</Link>}
                  {r.status === "OPEN" && <Button variant="secondary" small onClick={() => setCancelling(r)}>Cancel</Button>}</div>) },
              ]} />
            <Pagination page={page} pageSize={10} total={list.data?.total ?? 0} onChange={setPage} />
          </>
        )}
        <p className="small muted">When contributions arrive, create the exam plan and choose each contributed paper version for its subject. Subjects whose paper never arrives are left out when the exam is generated, and the exam page lists them.</p>
      </Card>
      {cancelling && <CancelRequest req={cancelling} onClose={() => setCancelling(null)} onDone={() => { setCancelling(null); list.reload(); }} />}
    </div>
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
