import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { Alert, Card, PageHeader, Spinner, StatCard } from "../components/ui";
import { useApi } from "../hooks/useApi";

type Dash = Record<string, unknown> & {
  role: string;
  upcoming_exams: number;
  unread_notifications: number;
};

const n = (value: unknown) =>
  typeof value === "number" ? value : 0;

export default function DashboardPage() {
  const { me } = useAuth();
  const { data, loading, error } = useApi<Dash>("/api/v1/dashboard");

  if (loading) return <Spinner />;
  if (error || !data) {
    return <Alert kind="error">{error || "Dashboard unavailable."}</Alert>;
  }

  const ops = (data.operations ?? {}) as Record<string, number>;
  const sec = (data.security ?? {}) as Record<string, number>;
  const role = data.role as keyof typeof ROLE_LABEL;

  return (
    <>
      <PageHeader
        title={`Welcome, ${me?.full_name.split(" ")[0]}`}
        subtitle={`${ROLE_LABEL[role]} dashboard · live data`}
      />

      <div className="stack">
        <div className="grid stats">
          {data.providers !== undefined && (
            <StatCard label="Active providers" value={n(data.providers)} />
          )}
          {data.branches !== undefined && (
            <StatCard label="Active branches" value={n(data.branches)} />
          )}
          {data.staff !== undefined && (
            <StatCard label="Active staff" value={n(data.staff)} />
          )}
          {data.teachers !== undefined && (
            <StatCard label="Teachers" value={n(data.teachers)} />
          )}
          {data.students !== undefined && (
            <StatCard label="Enrolled students" value={n(data.students)} />
          )}
          {data.batches !== undefined && (
            <StatCard label="Active batches" value={n(data.batches)} />
          )}
          <StatCard label="Upcoming exams" value={n(data.upcoming_exams)} />
          {data.pending_transfers !== undefined && (
            <StatCard
              label="Pending transfers"
              value={n(data.pending_transfers)}
              tone={n(data.pending_transfers) ? "warn" : undefined}
            />
          )}
          {data.open_tickets !== undefined && (
            <StatCard label="Open support tickets" value={n(data.open_tickets)} />
          )}
          {data.unpaid_invoices !== undefined && (
            <StatCard
              label="Unpaid invoices"
              value={n(data.unpaid_invoices)}
              tone={n(data.unpaid_invoices) ? "warn" : undefined}
            />
          )}
          <StatCard
            label="Unread notifications"
            value={n(data.unread_notifications)}
          />
        </div>

        {(ops.failed_deliveries > 0 ||
          ops.failed_generation_jobs > 0) && (
          <Alert kind="warn">
            Attention: {ops.failed_deliveries} exam deliveries and{" "}
            {ops.failed_generation_jobs} generation runs failed. See{" "}
            <Link to="/exams">Exams</Link>.
          </Alert>
        )}

        {(ops.failed_student_notifications > 0 ||
          ops.unfinished_corrections > 0) && (
          <Alert kind="warn">
            Attention: {ops.failed_student_notifications ?? 0} student
            notifications could not be delivered and{" "}
            {ops.unfinished_corrections ?? 0} result corrections are not
            finished. Unfinished corrections are completed by submitting the
            same correction again from the exam page.
          </Alert>
        )}

        {data.operations !== undefined && (
          <div className="grid cols-2">
            <Card>
              <h2>Operations</h2>
              <p>
                Pending deliveries to students:{" "}
                <strong>{ops.pending_deliveries}</strong>
              </p>
              <p>
                Failed deliveries: <strong>{ops.failed_deliveries}</strong>
              </p>
              <p>
                Failed generation runs:{" "}
                <strong>{ops.failed_generation_jobs}</strong>
              </p>
              <p>
                Failed student notifications:{" "}
                <strong>{ops.failed_student_notifications ?? 0}</strong>
              </p>
              <p>
                Unfinished result corrections:{" "}
                <strong>{ops.unfinished_corrections ?? 0}</strong>
              </p>
            </Card>

            <Card>
              <h2>Security (last 24h)</h2>
              <p>
                Denied requests: <strong>{sec.denied_events_24h}</strong>
              </p>
              <p>
                Failed sign-ins: <strong>{sec.failed_logins_24h}</strong>
              </p>
              <p className="small">
                <Link to="/audit">Open audit log</Link>
              </p>
            </Card>
          </div>
        )}

        {Array.isArray(data.teaching) && (
          <Card>
            <h2>My teaching assignments</h2>
            {(data.teaching as { subject: string; branch_id: string }[])
              .length === 0 ? (
              <p className="muted">
                No teaching assignments yet. Ask your branch admin.
              </p>
            ) : (
              <ul>
                {(
                  data.teaching as {
                    subject: string;
                    branch_id: string;
                  }[]
                ).map((assignment, index) => (
                  <li key={index}>
                    {assignment.subject}{" "}
                    <span className="muted small">
                      ({assignment.branch_id.slice(0, 8)})
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
    </>
  );
}
