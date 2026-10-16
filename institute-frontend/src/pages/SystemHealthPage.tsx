import { Alert, Badge, Card, DataTable, PageHeader, Spinner, StatCard } from "../components/ui";
import { useApi } from "../hooks/useApi";

type Health = {
  checked_at: string; healthy: boolean;
  services: Record<string, { status: string; database?: string; redis?: string }>;
  queues: Record<string, number>;
};

const LABEL: Record<string, string> = {
  identity: "Identity and access", organization: "Organization", academics: "Academics", enrollment: "Enrollment",
  paper: "Questions and papers", exam: "Exam management", operations: "Operations",
  failed_exam_deliveries: "Exams that could not be delivered to the Student services", failed_generation_jobs: "Failed exam generation runs",
  failed_student_notifications: "Student notifications that could not be delivered", failed_staff_notifications: "Staff notifications that could not be delivered",
  unfinished_result_corrections: "Result corrections not yet applied",
};

export default function SystemHealthPage() {
  const h = useApi<Health>("/api/v1/system/health");
  if (h.loading) return <Spinner />;
  if (h.error || !h.data) return <Alert kind="error">{h.error || "Health information is unavailable."}</Alert>;
  const rows = Object.entries(h.data.services).map(([name, s]) => ({ name, ...s }));
  return (
    <>
      <PageHeader title="Service health" subtitle={`Checked ${new Date(h.data.checked_at).toLocaleTimeString()} · refresh the page to check again`} />
      <div className="stack">
        <Alert kind={h.data.healthy ? "success" : "warn"}>{h.data.healthy ? "Every service answers and no work is waiting on an operator." : "Something needs attention: see the services and queues below."}</Alert>
        <Card>
          <h2>Services</h2>
          <DataTable rows={rows} rowKey={(r) => r.name} columns={[
            { header: "Service", render: (r) => LABEL[r.name] ?? r.name },
            { header: "Status", render: (r) => <Badge tone={r.status === "ok" ? "ok" : "danger"}>{r.status.replace(/_/g, " ")}</Badge> },
            { header: "Database", render: (r) => r.database ?? "—" },
            { header: "Redis", render: (r) => r.redis ?? "—" },
          ]} />
        </Card>
        <Card>
          <h2>Work waiting for an operator</h2>
          <div className="grid stats">
            {Object.entries(h.data.queues).map(([k, v]) => <StatCard key={k} label={LABEL[k] ?? k} value={v} tone={v > 0 ? "warn" : undefined} />)}
          </div>
        </Card>
      </div>
    </>
  );
}
