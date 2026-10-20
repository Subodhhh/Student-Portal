import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, DataTable, EmptyState, PageHeader, Pagination, SelectField, Spinner, StatusBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";
import { TEST_TYPE_LABEL, type ScheduledTest } from "../services/createTest";

// Test -> Scheduled Tests: Daily, Weekly and Global tests the caller may see (the API scopes the list: admins their branches,
// teachers the tests that target students they teach one of the test's subjects to).
const STATE_TEXT: Record<string, string> = {
  PUBLISHED: "Published and delivered", PUBLISHED_DELIVERY_PENDING: "Published, delivery in progress", DELIVERY_FAILED: "Published, delivery failed (retrying)",
  PUBLICATION_FAILED: "Not published (failed)", PUBLISHING: "Publishing", CANCELLED: "Cancelled", NOT_PUBLISHED: "Not published",
};

export default function ScheduledTestsPage() {
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [type, setType] = useState("");
  const list = useApi<Page<ScheduledTest>>(`/api/v1/tests?page=${page}&page_size=25${type ? `&test_type=${type}` : ""}`);
  const fmt = (v: string) => new Date(v).toLocaleString();
  return (
    <div className="stack">
      <PageHeader subtitle="Daily, Weekly and Global tests you may see, with their state, schedule and audience."
        actions={can("test.create") && <div className="row"><Link className="btn" to="/tests/new">Daily Test</Link><Link className="btn secondary" to="/tests/weekly/new">Weekly Test</Link></div>} />
      <div className="toolbar">
        <SelectField label="Type" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }}>
          <option value="">All types</option><option value="DAILY">Daily</option><option value="WEEKLY">Weekly</option><option value="GLOBAL">Global</option>
        </SelectField>
      </div>
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(t) => t.plan_id}
            empty={<EmptyState title="No scheduled tests yet" hint={can("test.create") ? "Create a Daily or Weekly Test to see it here." : undefined} />}
            columns={[
              { header: "Test", render: (t) => <>{t.exam_id ? <Link to={`/exams/${t.exam_id}`}>{t.name}</Link> : t.name}
                {t.series_id && <div className="muted small"><Link to={`/tests/weekly/${t.series_id}`}>Weekly Test plan</Link></div>}</> },
              { header: "Type", render: (t) => <Badge tone={t.test_type === "GLOBAL" ? "info" : undefined}>{TEST_TYPE_LABEL[t.test_type] ?? t.test_type}</Badge> },
              { header: "State", render: (t) => <span title={t.error ?? undefined}><StatusBadge status={t.state} /> <span className="muted small">{STATE_TEXT[t.state] ?? ""}</span></span> },
              { header: "Window", render: (t) => `${fmt(t.start_at)} – ${fmt(t.end_at)}` },
              { header: "Subjects", render: (t) => t.subjects.join(", ") },
              { header: "Audience", render: (t) => [t.audience.branches.length ? `branches: ${t.audience.branches.join(", ")}` : "", t.audience.batches.length ? `batches: ${t.audience.batches.join(", ")}` : "",
                t.audience.students ? `${t.audience.students} individual student(s)` : ""].filter(Boolean).join(" · ") || t.audience.type },
              { header: "Assigned", render: (t) => t.assigned_students },
              { header: "Questions / marks", render: (t) => (t.question_count == null ? "—" : `${t.question_count} / ${t.total_marks}`) },
              { header: "Provider / branch", render: (t) => [t.provider_name, t.home_branch].filter(Boolean).join(" · ") },
            ]} />
          {list.data && <Pagination page={page} pageSize={25} total={list.data.total} onChange={setPage} />}
        </>
      )}
    </div>
  );
}
