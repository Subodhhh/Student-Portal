import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isGlobalRole } from "../auth/roles";
import { GlobalTestForm, GlobalTestsPanel, type GlobalTest } from "../components/globalTests";
import { Alert, Badge, Button, Card, ConfirmDialog, DataTable, EmptyState, Modal, PageHeader, Pagination, SelectField, Spinner, StatusBadge, Tabs, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api, type Page } from "../services/api";
import type { ScheduledTest } from "../services/createTest";

// Test -> Global Tests. The platform creates, publishes and withdraws Global Tests and reviews contributions; provider staff schedule
// published tests for their students and offer their own approved papers. A contribution never changes a published test: an approved
// paper is copied into a platform paper that is approved normally and only then placed into a new test version by the platform.

export type Contribution = {
  contribution_id: string; test_id: string; test_name: string; test_status: string; subject_id: string; subject: string | null; provider_name: string | null;
  branch_name: string | null; paper_title: string; paper_version_id: string; version_no: number; version_status: string; question_count: number; total_marks: string;
  note: string | null; status: string; submitted_by_name: string | null; submitted_at: string; reviewed_by_name: string | null; reviewed_at: string | null;
  review_note: string | null; copy_status: string | null; copy_error: string | null; global_paper_id: string | null; global_paper_version_id: string | null;
  global_version_status: string | null; competing: number | null; section_exists: boolean | null; can_withdraw: boolean;
};
type Option = { paper_id: string; paper_title: string; subject: string | null; paper_version_id: string; version_no: number; version_status: string; question_count: number; total_marks: string };

export default function GlobalTestsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "tests";
  const { me, can } = useAuth();
  const platform = isGlobalRole(me?.primary_role) && can("examplan.manage");
  const [contributeTo, setContributeTo] = useState<GlobalTest | null>(null);
  const [notice, setNotice] = useState("");
  return (
    <div className="stack">
      <PageHeader subtitle={platform ? "Platform-owned tests. Providers schedule published tests and offer papers; you review the offers and decide what goes into a new test version."
        : "Tests published by the platform. Schedule one for your students, or offer one of your approved papers for the platform to review."} />
      <Tabs active={tab} onChange={(id) => setParams({ tab: id }, { replace: true })}
        tabs={[{ id: "tests", label: "Global Tests" }, { id: "contributions", label: platform ? "Contributions to review" : "My contributions" }]} />
      {notice && <Alert kind="success">{notice}</Alert>}
      {tab === "contributions" ? <ContributionsPanel platform={platform} />
        : <GlobalTestsPanel onContribute={!platform && can("paper.publish") ? setContributeTo : undefined} />}
      {can("examplan.manage") && <p className="muted small">Composite Tests are retired. Earlier composite requests remain available in the <Link to="/composite">composite history</Link>.</p>}
      {contributeTo && <ContributeDialog test={contributeTo} onClose={() => setContributeTo(null)}
        onDone={(c) => { setContributeTo(null); setNotice(`Offered “${c.paper_title}” (${c.subject}) for ${c.test_name}. The platform will review it.`); setParams({ tab: "contributions" }, { replace: true }); }} />}
    </div>
  );
}

function ContributeDialog({ test, onClose, onDone }: { test: GlobalTest; onClose: () => void; onDone: (c: Contribution) => void }) {
  const options = useApi<{ items: Option[] }>(`/api/v1/global-tests/${test.test_id}/contribution-options`);
  const [picked, setPicked] = useState("");
  const [note, setNote] = useState("");
  const { busy, error, run } = useAction();
  const items = options.data?.items ?? [];
  return (
    <Modal title={`Offer a paper for “${test.name}”`} onClose={onClose}>
      <p className="muted small">Choose one of your approved or published papers. The platform reviews it; if approved, a platform copy is made and may be used in a
        future version of this test. The published test itself never changes, and your paper stays yours.</p>
      {error && <Alert kind="error">{error}</Alert>}
      {options.error && <Alert kind="error">{options.error}</Alert>}
      {options.loading && !options.data ? <Spinner /> : items.length === 0 ? (
        <EmptyState title="No paper available to offer" hint="Only approved papers you may publish (your provider, branch and subjects) can be offered." />
      ) : (
        <SelectField label="Paper" value={picked} onChange={(e) => setPicked(e.target.value)}>
          <option value="">Choose a paper</option>
          {items.map((o) => <option key={o.paper_version_id} value={o.paper_version_id}>{o.subject}: {o.paper_title} v{o.version_no} ({o.version_status.toLowerCase()}) · {o.question_count} questions · {Number(o.total_marks)} marks</option>)}
        </SelectField>
      )}
      <TextAreaField label="Note for the reviewer (optional)" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!picked} onClick={() => void run(async () => {
          onDone(await api<Contribution>(`/api/v1/global-tests/${test.test_id}/contributions`, { method: "POST", body: { paper_version_id: picked, note: note.trim() || null } }));
        })}>Offer paper</Button>
      </div>
    </Modal>
  );
}

function ContributionsPanel({ platform, testId }: { platform: boolean; testId?: string }) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState(platform ? "PENDING" : "");
  const [action, setAction] = useState<{ kind: "approve" | "reject" | "withdraw"; c: Contribution } | null>(null);
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const act = useAction();
  const list = useApi<Page<Contribution>>(`/api/v1/global-test-contributions?page=${page}&page_size=25${status ? `&status=${status}` : ""}${testId ? `&test_id=${testId}` : ""}`);
  const finish = (msg: string) => { setNotice(msg); setAction(null); setText(""); list.reload(); };
  const retryCopy = (c: Contribution) => act.run(async () => {
    await api(`/api/v1/global-test-contributions/${c.contribution_id}/retry-copy`, { method: "POST" });
    finish(`The platform copy of “${c.paper_title}” was made.`);
  }).finally(list.reload);
  const decide = () => {
    if (!action) return;
    const { kind, c } = action;
    void act.run(async () => {
      try {
        await api(`/api/v1/global-test-contributions/${c.contribution_id}/${kind}`, { method: "POST", body: kind === "approve" ? { note: text.trim() || null } : { reason: text } });
      } catch (e) {
        if (e instanceof ApiError && e.code === "COPY_FAILED") { list.reload(); setAction(null); }       // approved and recorded; the copy is retried from the list
        throw e;
      }
      finish(kind === "approve" ? `Approved “${c.paper_title}”. Its platform copy now needs the normal paper approval (Question Bank & Papers) before it can be placed in a new test version.`
        : kind === "reject" ? `Rejected “${c.paper_title}”.` : `Withdrew “${c.paper_title}”.`);
    });
  };
  return (
    <div className="stack">
      <div className="toolbar">
        <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All</option><option value="PENDING">Pending</option><option value="APPROVED">Approved</option><option value="REJECTED">Rejected</option><option value="WITHDRAWN">Withdrawn</option>
        </SelectField>
      </div>
      {notice && <Alert kind="success">{notice}</Alert>}
      {act.error && !action && <Alert kind="error">{act.error}</Alert>}
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(c) => c.contribution_id}
            empty={<EmptyState title={platform ? "No contributions" : "You have not offered any papers"} hint={platform ? undefined : testId ? "Choose “Offer a paper” above." : "Open a published Global Test and choose “Offer a paper”."} />}
            columns={[
              { header: "Global Test", render: (c) => <><strong>{c.test_name}</strong>{c.test_status !== "PUBLISHED" && <div className="muted small">{c.test_status.toLowerCase()}</div>}</> },
              { header: "Subject", render: (c) => <>{c.subject}{platform && (c.competing ?? 0) > 1 && <div><Badge tone="warn">Conflict: {c.competing} offers</Badge></div>}
                {platform && c.section_exists && <div className="muted small">The test already has this subject</div>}</> },
              { header: "Paper", render: (c) => <>{c.paper_title} v{c.version_no}<div className="muted small">{c.question_count} questions · {Number(c.total_marks)} marks</div>{c.note && <div className="muted small">“{c.note}”</div>}</> },
              { header: "From", render: (c) => <>{[c.provider_name, c.branch_name].filter(Boolean).join(" · ")}<div className="muted small">{c.submitted_by_name} · {new Date(c.submitted_at).toLocaleDateString()}</div></> },
              { header: "Status", render: (c) => <><StatusBadge status={c.status} />{c.review_note && <div className="muted small">{c.review_note}</div>}
                {c.copy_status === "FAILED" && <div><Badge tone="danger">Copy failed</Badge> <span className="muted small">{c.copy_error}</span></div>}
                {c.copy_status === "COPIED" && <div className="muted small">Platform copy: {c.global_version_status?.toLowerCase()}</div>}</> },
              { header: "Actions", render: (c) => (
                <div className="row">
                  {platform && c.status === "PENDING" && <Button small onClick={() => { setText(""); setAction({ kind: "approve", c }); }}>Approve</Button>}
                  {platform && c.status === "PENDING" && <Button small variant="danger" onClick={() => { setText(""); setAction({ kind: "reject", c }); }}>Reject</Button>}
                  {platform && c.status === "APPROVED" && c.copy_status !== "COPIED" && <Button small variant="secondary" busy={act.busy} onClick={() => void retryCopy(c)}>Retry copy</Button>}
                  {platform && c.global_paper_version_id && <Link className="btn small secondary" to={`/papers/${c.global_paper_version_id}`}>Review platform copy</Link>}
                  {c.can_withdraw && <Button small variant="secondary" onClick={() => { setText(""); setAction({ kind: "withdraw", c }); }}>Withdraw</Button>}
                </div>
              ) },
            ]} />
          <Pagination page={page} pageSize={25} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
      {action && (
        <ConfirmDialog title={`${action.kind === "approve" ? "Approve" : action.kind === "reject" ? "Reject" : "Withdraw"} “${action.c.paper_title}”`}
          danger={action.kind !== "approve"} busy={act.busy} confirmLabel={action.kind[0].toUpperCase() + action.kind.slice(1)}
          message={action.kind === "approve"
            ? `A platform copy of this ${action.c.subject} paper is made. ${(action.c.competing ?? 0) > 1 ? "Other providers also offered this subject: approving this one blocks theirs until you reject them. " : ""}The published test does not change.`
            : action.kind === "reject" ? "The provider is told your reason." : "The platform will no longer review this paper."}
          onCancel={() => { setAction(null); setText(""); }} onConfirm={decide}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          <TextAreaField label={action.kind === "approve" ? "Note (optional)" : "Reason (required)"} value={text} onChange={(e) => setText(e.target.value)} />
        </ConfirmDialog>
      )}
    </div>
  );
}

const fmt = (v: string) => new Date(v).toLocaleString();

/** One Global Test, step by step: its configuration, publication and versions (platform only), the papers providers offered for it
 * and their conflicts, and its schedules. Every action calls the same endpoints as the list; the backend decides who may do what. */
export function GlobalTestDetailPage() {
  const { testId = "" } = useParams();
  const navigate = useNavigate();
  const { me, can } = useAuth();
  const platform = isGlobalRole(me?.primary_role) && can("examplan.manage");
  const t = useApi<GlobalTest>(`/api/v1/global-tests/${testId}`);
  const schedules = useApi<Page<ScheduledTest>>(`/api/v1/tests?global_test_id=${testId}&page_size=100`);
  const act = useAction();
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState<"publish" | "withdraw" | null>(null);
  const [reason, setReason] = useState("");
  const [contributing, setContributing] = useState(false);
  const [notice, setNotice] = useState("");
  if (t.loading && !t.data) return <Spinner />;
  if (t.error || !t.data) return <Alert kind="error">{t.error || "Global Test not found."}</Alert>;
  const g = t.data;
  const transition = (change: "publish" | "withdraw") => act.run(async () => {
    await api(`/api/v1/global-tests/${g.test_id}/${change}`, { method: "POST", body: change === "withdraw" ? { reason } : undefined });
    setConfirm(null); setReason("");
    setNotice(change === "publish" ? "Published. Its papers, duration and marking can no longer change." : "Withdrawn. Schedules stop generating; generated exams, attempts and results are unchanged.");
    t.reload(); schedules.reload();
  });
  const newVersion = () => act.run(async () => {
    const copy = await api<GlobalTest>(`/api/v1/global-tests/${g.test_id}/derive`, { method: "POST" });
    navigate(`/tests/global/${copy.test_id}`);
  });
  const generate = (s: ScheduledTest) => act.run(async () => {
    const job = await api<{ status: string; error: string | null }>(`/api/v1/exam-plans/${s.plan_id}/generate`, { method: "POST", body: {},
      headers: { "Idempotency-Key": `global-${s.plan_id}` } });
    setNotice(`Generation ${job.status.toLowerCase()}${job.error ? `: ${job.error}` : ""}.`);
    schedules.reload();
  });
  return (
    <div className="stack">
      <PageHeader crumbs={[<Link key="g" to="/tests/global">Global Tests</Link>, g.name]} title={g.name}
        subtitle={`${g.sections.length} subject(s) · ${g.question_count} questions · ${Number(g.total_marks)} marks · ${g.duration_minutes} min · per-question marking`}
        actions={<StatusBadge status={g.status} />} />
      {notice && <Alert kind="success">{notice}</Alert>}
      {act.error && !confirm && <Alert kind="error">{act.error}</Alert>}
      {g.derived_from_test_id && <p className="muted small">A new version of <Link to={`/tests/global/${g.derived_from_test_id}`}>an earlier Global Test</Link>; that test is unchanged.</p>}

      <Card>
        <div className="card-head"><h2>1. Configuration</h2>
          {platform && <div className="row">
            {g.status === "DRAFT" && <Button variant="secondary" onClick={() => setEditing(true)}>Edit draft</Button>}
            {g.status === "DRAFT" && <Button onClick={() => setConfirm("publish")}>Publish</Button>}
            {g.status === "PUBLISHED" && <Button variant="danger" onClick={() => setConfirm("withdraw")}>Withdraw</Button>}
            {g.status !== "DRAFT" && <Button variant="secondary" busy={act.busy} onClick={() => void newVersion()}>Create new version</Button>}
          </div>}
        </div>
        <p className="muted small">{g.status === "DRAFT" ? "A draft: platform administrators choose one approved platform paper per subject, then publish it."
          : "Published tests never change. A change is a new version (a draft copy) that is published separately; schedules, attempts and results of this version stay as they are."}</p>
        <DataTable rows={g.sections} rowKey={(x) => x.paper_version_id} empty={<EmptyState title="No sections yet" />} columns={[
          { header: "Subject", render: (x) => x.subject ?? "Subject" }, { header: "Paper", render: (x) => `${x.paper_title} v${x.version_no}` },
          { header: "Paper status", render: (x) => <StatusBadge status={x.version_status} /> }, { header: "Questions", render: (x) => x.question_count },
          { header: "Marks", render: (x) => Number(x.total_marks) },
        ]} />
      </Card>

      <Card>
        <div className="card-head"><h2>2. Contributions</h2>
          {!platform && can("paper.publish") && g.status === "PUBLISHED" && <Button variant="secondary" onClick={() => setContributing(true)}>Offer a paper</Button>}</div>
        <p className="muted small">{platform ? "Papers providers offered for this test. Approve one per subject (competing offers are marked as conflicts), approve and publish its platform copy in Question Bank & Papers, then use it in a new version."
          : "Papers your provider offered for this test, and their review state. The platform decides; the published test never changes."}</p>
        <ContributionsPanel platform={platform} testId={g.test_id} />
      </Card>

      <Card>
        <div className="card-head"><h2>3. Schedules</h2>
          {g.status === "PUBLISHED" && can("examplan.manage") && <Link className="btn" to={`/tests/global/${g.test_id}/schedule`}>Schedule this Global Test</Link>}</div>
        <p className="muted small">Each schedule assigns this exact test to one branch's students for one window. Exams are generated automatically at the chosen time, or now.</p>
        {schedules.error && <Alert kind="error">{schedules.error}</Alert>}
        {schedules.loading && !schedules.data ? <Spinner /> : (
          <DataTable rows={schedules.data?.items ?? []} rowKey={(x) => x.plan_id} empty={<EmptyState title="Not scheduled yet" />} columns={[
            { header: "Schedule", render: (x) => (x.exam_id ? <Link to={`/exams/${x.exam_id}`}>{x.name}</Link> : x.name) },
            { header: "Provider / branch", render: (x) => [x.provider_name, x.home_branch].filter(Boolean).join(" · ") },
            { header: "Window", render: (x) => `${fmt(x.start_at)} – ${fmt(x.end_at)}` },
            { header: "State", render: (x) => <span title={x.error ?? undefined}><StatusBadge status={x.state} /></span> },
            { header: "Assigned", render: (x) => (x.exam_id ? x.assigned_students : "at generation") },
            { header: "", render: (x) => (!x.exam_id && x.state !== "CANCELLED" && can("exam.generate") && g.status === "PUBLISHED"
              ? <Button small variant="secondary" busy={act.busy} onClick={() => void generate(x)}>Generate now</Button> : null) },
          ]} />
        )}
      </Card>

      {editing && <GlobalTestForm test={g} onClose={() => setEditing(false)} onDone={() => { setEditing(false); setNotice("Draft saved."); t.reload(); }} />}
      {contributing && <ContributeDialog test={g} onClose={() => setContributing(false)}
        onDone={(c) => { setContributing(false); setNotice(`Offered “${c.paper_title}” (${c.subject}). The platform will review it.`); }} />}
      {confirm && (
        <ConfirmDialog title={`${confirm === "publish" ? "Publish" : "Withdraw"} “${g.name}”`} danger={confirm === "withdraw"} busy={act.busy}
          confirmLabel={confirm === "publish" ? "Publish" : "Withdraw"} onCancel={() => { setConfirm(null); setReason(""); }} onConfirm={() => void transition(confirm)}
          message={confirm === "publish" ? "Providers can then schedule it. After publishing, its papers, duration and marking can never change."
            : "Providers can no longer schedule it and its schedules stop generating. Exams already generated, attempts and results are unchanged."}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          {confirm === "withdraw" && <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />}
        </ConfirmDialog>
      )}
    </div>
  );
}
