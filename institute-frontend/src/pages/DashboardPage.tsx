import { Link } from "react-router-dom";
import { useAuth, type Role } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { CATEGORIES, isGlobalRole } from "../auth/roles";
import { Alert, Button, Card, DataTable, EmptyState, PageHeader, Spinner, StatCard, StatusBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";
import { StudentCount } from "../components/StudentCount";

type Dash = Record<string, unknown> & {
  role: string;
  upcoming_exams: number;
  unread_notifications: number;
};

// Fields the backend adds for global roles (Super Admin / Admin). Every value is a count straight from the database.
type Platform = {
  providers_by_status: Record<string, number>;
  branches_by_status: Record<string, number>;
  students: number;
  active_enrollments: number;
  staff: number;
  staff_by_role: Partial<Record<Role, number>>;
  subjects: number;
  subjects_offered: number;
  questions: number;
  papers_by_status: Record<string, number>;
  composite_by_status: Record<string, number>;
  exams_by_status: Record<string, number>;
  recent_exams_30d: number;
  papers_awaiting_approval: number;
  rejected_uploads_7d: number;
  global_tests_published: number;
  upcoming_exam_list: { exam_id: string; name: string; status: string; start_at: string; provider_name: string | null; branch_name: string | null }[];
  open_tickets: number;
  unpaid_invoices: number;
  security: { denied_events_24h: number; failed_logins_24h: number };
  operations: Record<string, number>;
};

type AuditRow = { audit_id: string; at: string; action: string; target_type: string | null; outcome: string; reason: string | null;
  actor: string | null; actor_name: string | null; provider_name: string | null };

const n = (value: unknown) =>
  typeof value === "number" ? value : 0;
const when = (s: string) => new Date(s).toLocaleString();
const sum = (r?: Record<string, number>) => Object.values(r ?? {}).reduce((a, b) => a + b, 0);

export default function DashboardPage() {
  const { me } = useAuth();
  const { data, loading, error, reload } = useApi<Dash>("/api/v1/dashboard");

  if (loading && !data) return <Spinner label="Loading dashboard" />;
  if (error || !data) {
    // Never fall back to zeros: an unavailable dashboard says so and offers a retry.
    return (
      <Alert kind="error">
        The dashboard could not be loaded{error ? `: ${error}` : "."} No figures are shown rather than possibly wrong ones.{" "}
        <Button small variant="secondary" onClick={reload}>Retry</Button>
      </Alert>
    );
  }
  if (isGlobalRole(me?.primary_role) && "providers_by_status" in data) {
    return <PlatformDashboard d={data as unknown as Platform & Dash} />;
  }
  return <RoleDashboard data={data} />;
}

// ------------------------------------------------------------------ Super Admin / Admin

function PlatformDashboard({ d }: { d: Platform & Dash }) {
  const { me, can } = useAuth();
  const ops = d.operations ?? {};
  const denied = useApi<Page<AuditRow>>(can("audit.read") ? "/api/v1/audit-logs?outcome=DENIED&page_size=5" : null);
  const recent = useApi<Page<AuditRow>>(can("audit.read") ? "/api/v1/audit-logs?outcome=SUCCESS&page_size=8" : null);
  const activeProviders = d.providers_by_status.ACTIVE ?? 0;
  const inactiveProviders = d.providers_by_status.INACTIVE ?? 0;
  const published = d.papers_by_status?.PUBLISHED ?? 0;
  const attention = n(ops.failed_deliveries) + n(ops.failed_generation_jobs) + n(ops.failed_student_notifications) + n(ops.unfinished_corrections);

  return (
    <>
      <PageHeader subtitle={`Welcome, ${me?.full_name.split(" ")[0]}. Live platform overview.`} />
      <div className="stack">
        {attention > 0 && (
          <Alert kind="warn">
            <strong>Needs attention:</strong>{" "}
            {[
              n(ops.failed_deliveries) > 0 && `${ops.failed_deliveries} exam deliveries failed`,
              n(ops.failed_generation_jobs) > 0 && `${ops.failed_generation_jobs} exam generation runs failed`,
              n(ops.failed_student_notifications) > 0 && `${ops.failed_student_notifications} student notifications were not delivered`,
              n(ops.unfinished_corrections) > 0 && `${ops.unfinished_corrections} result corrections are unfinished (submit the same correction again from the exam page)`,
            ].filter(Boolean).join(" · ")}.{" "}
            <Link to="/exams">Open Exams</Link>
          </Alert>
        )}

        <nav className="quick-actions" aria-label="Quick actions">
          {can("provider.create") && <Link className="btn secondary small" to="/providers">Add a provider</Link>}
          {can("staff.create") && <Link className="btn secondary small" to="/staff">Manage accounts</Link>}
          {can("examplan.manage") && <Link className="btn secondary small" to="/exam-plans/new">Create a test</Link>}
          {can("subject.manage") && <Link className="btn secondary small" to="/subjects">Subject catalogue</Link>}
          {can("paper.read") && <Link className="btn secondary small" to="/papers?tab=imports">Import processing</Link>}
        </nav>

        {(d.papers_awaiting_approval > 0 || d.rejected_uploads_7d > 0) && (
          <Alert kind="info">
            {d.papers_awaiting_approval > 0 && <>{d.papers_awaiting_approval} paper version(s) are waiting for approval. </>}
            {d.rejected_uploads_7d > 0 && <>{d.rejected_uploads_7d} paper upload(s) were rejected in the last 7 days. </>}
            <Link to="/papers?tab=papers">Question Bank & Papers</Link>
          </Alert>
        )}

        <section aria-labelledby="overview-h">
          <h2 id="overview-h" className="section-title">Platform overview</h2>
          <div className="grid stats">
            <StatCard label="Active providers" value={activeProviders} to="/providers" hint={inactiveProviders ? `${inactiveProviders} inactive` : "None inactive"} />
            <StatCard label="Active branches" value={d.branches_by_status.ACTIVE ?? 0} to="/providers" hint={`${sum(d.branches_by_status)} in total`} />
            <StatCard label="Enrolled students" value={can("enrollment.manage") ? <StudentCount count={d.students} url="/api/v1/students?status=ACTIVE" title="Enrolled students"
              context="Every student with at least one active enrollment." /> : d.students} hint={<><Link to="/students">Open the directory</Link> · {d.active_enrollments} active enrollments</>} />
            <StatCard label="Active staff" value={d.staff} to="/staff" />
            <StatCard label="Platform subjects" value={d.subjects} to="/subjects" hint={`${d.subjects_offered} offered by providers`} />
            <StatCard label="Bank questions" value={d.questions} to="/papers" hint={`${published} published papers`} />
            <StatCard label="Upcoming exams" value={n(d.upcoming_exams)} to="/exams" hint={`${d.recent_exams_30d} completed in 30 days`} />
            <StatCard label="Global Tests" value={n(d.global_tests_published)} to="/tests/global" hint="published" />
            <StatCard label="Open support requests" value={d.open_tickets} to="/support" tone={d.open_tickets ? "warn" : undefined} />
            {d.unpaid_invoices > 0 && <StatCard label="Unpaid invoices" value={d.unpaid_invoices} to="/billing" tone="warn" />}
            <StatCard label="Unread notifications" value={n(d.unread_notifications)} to="/notifications" />
          </div>
        </section>

        <div className="grid cols-2">
          <Card>
            <div className="card-head"><h2>Staff by role</h2><Link to="/staff" className="small">Role Management</Link></div>
            <table className="compact">
              <caption className="sr-only">Active staff per role</caption>
              <thead><tr><th scope="col">Role</th><th scope="col">People</th></tr></thead>
              <tbody>
                {CATEGORIES.map((c) => (
                  <tr key={c.role}><td><Link to={`/staff/${c.slug}`}>{ROLE_LABEL[c.role]}</Link></td><td>{d.staff_by_role[c.role] ?? 0}</td></tr>
                ))}
              </tbody>
            </table>
            <p className="muted small">People holding an active role; someone with two roles counts in both.</p>
          </Card>

          <Card>
            <div className="card-head"><h2>Upcoming exams</h2><Link to="/exams" className="small">Exams</Link></div>
            <DataTable rows={d.upcoming_exam_list} rowKey={(e) => e.exam_id} empty={<EmptyState title="No upcoming exams" />}
              columns={[
                { header: "Exam", render: (e) => e.name },
                { header: "Provider / branch", render: (e) => [e.provider_name, e.branch_name].filter(Boolean).join(" / ") || "—" },
                { header: "Starts", render: (e) => when(e.start_at) },
              ]} />
          </Card>
        </div>

        <div className="grid cols-2">
          <Card>
            <div className="card-head"><h2>Question bank & papers</h2><Link to="/papers" className="small">Open</Link></div>
            <StatusCounts counts={d.papers_by_status} empty="No papers yet" />
          </Card>
          <Card>
            <div className="card-head"><h2>Composite tests</h2><Link to="/composite" className="small">Open</Link></div>
            <StatusCounts counts={d.composite_by_status} empty="No composite test requests yet" />
          </Card>
        </div>

        {recent.data && (
          <Card>
            <div className="card-head"><h2>Recent platform activity</h2><Link to="/audit" className="small">Audit Log</Link></div>
            <DataTable rows={recent.data.items} rowKey={(a) => a.audit_id} empty={<EmptyState title="No activity recorded yet" />}
              columns={[
                { header: "When", render: (a) => when(a.at) },
                { header: "Who", render: (a) => a.actor_name ?? a.actor ?? "system" },
                { header: "Action", render: (a) => <code>{a.action}</code> },
                { header: "Provider", render: (a) => a.provider_name ?? "—" },
              ]} />
          </Card>
        )}
        {recent.error && <Alert kind="error">Recent activity is unavailable: {recent.error} <Button small variant="secondary" onClick={recent.reload}>Retry</Button></Alert>}

        <Card>
          <div className="card-head"><h2>Security, last 24 hours</h2>{can("audit.read") && <Link to="/audit" className="small">Audit Log</Link>}</div>
          <p>Denied requests <strong>{d.security.denied_events_24h}</strong> · Failed sign-ins <strong>{d.security.failed_logins_24h}</strong></p>
          {denied.data && (
            <DataTable rows={denied.data.items} rowKey={(a) => a.audit_id} empty={<EmptyState title="No denied requests recorded" />}
              columns={[
                { header: "When", render: (a) => when(a.at) },
                { header: "Action", render: (a) => a.action },
                { header: "Target", render: (a) => a.target_type ?? "—" },
                { header: "Outcome", render: (a) => <StatusBadge status={a.outcome} /> },
              ]} />
          )}
        </Card>
      </div>
    </>
  );
}

function StatusCounts({ counts, empty }: { counts?: Record<string, number>; empty: string }) {
  const entries = Object.entries(counts ?? {}).filter(([, v]) => v > 0);
  if (entries.length === 0) return <p className="muted">{empty}</p>;
  return (
    <ul className="plain status-counts">
      {entries.map(([status, count]) => <li key={status}><StatusBadge status={status} /> <strong>{count}</strong></li>)}
    </ul>
  );
}

// ------------------------------------------------------------------ provider, branch and teacher dashboards

function RoleDashboard({ data }: { data: Dash }) {
  const { me, can } = useAuth();
  const role = data.role as keyof typeof ROLE_LABEL;
  return (
    <>
      <PageHeader subtitle={`Welcome, ${me?.full_name.split(" ")[0]} · ${ROLE_LABEL[role]} · live data`} />

      <div className="stack">
        <div className="grid stats">
          {data.branches !== undefined && <StatCard label="Active branches" value={n(data.branches)} />}
          {data.teachers !== undefined && <StatCard label="Teachers" value={n(data.teachers)} />}
          {data.students !== undefined && <StatCard label="Enrolled students" value={can("enrollment.manage") ? <StudentCount count={Number(data.students)} url="/api/v1/students?status=ACTIVE" title="Your enrolled students"
            context="Students with an active enrollment in your institute or branches." /> : n(data.students)} hint={<Link to="/students">Open the directory</Link>} />}
          {data.batches !== undefined && <StatCard label="Active batches" value={n(data.batches)} to="/batches" />}
          <StatCard label="Upcoming exams" value={n(data.upcoming_exams)} to="/exams" />
          {data.pending_transfers !== undefined && (
            <StatCard label="Pending transfers" value={n(data.pending_transfers)} tone={n(data.pending_transfers) ? "warn" : undefined} to="/students" />
          )}
          {data.open_tickets !== undefined && <StatCard label="Open support tickets" value={n(data.open_tickets)} to="/support" />}
          {data.unpaid_invoices !== undefined && (
            <StatCard label="Unpaid invoices" value={n(data.unpaid_invoices)} tone={n(data.unpaid_invoices) ? "warn" : undefined} to="/billing" />
          )}
          {data.my_papers !== undefined && <StatCard label="My papers" value={n(data.my_papers)} to="/papers" />}
          <StatCard label="Unread notifications" value={n(data.unread_notifications)} to="/notifications" />
        </div>

        {Array.isArray(data.teaching) && (
          <Card>
            <h2>My teaching assignments</h2>
            {(data.teaching as { subject: string }[]).length === 0 ? (
              <p className="muted">No teaching assignments yet. Ask your branch admin.</p>
            ) : (
              <ul>
                {(data.teaching as { subject: string; branch_id: string }[]).map((assignment, index) => (
                  <li key={index}>{assignment.subject}</li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
    </>
  );
}
