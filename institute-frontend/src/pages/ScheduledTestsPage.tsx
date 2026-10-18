import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, DataTable, EmptyState, PageHeader, Pagination, Spinner, StatusBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";
import type { ScheduledTest } from "../services/createTest";

// Test -> Scheduled Tests: the tests the caller may see (the API scopes the list: teachers see their own and those of the subjects they teach).
const STATE_TEXT: Record<string, string> = {
  PUBLISHED: "Published and delivered", PUBLISHED_DELIVERY_PENDING: "Published, delivery in progress", DELIVERY_FAILED: "Published, delivery failed (retrying)",
  PUBLICATION_FAILED: "Not published (failed)", PUBLISHING: "Publishing", CANCELLED: "Cancelled", NOT_PUBLISHED: "Not published",
};

export default function ScheduledTestsPage() {
  const [page, setPage] = useState(1);
  const list = useApi<Page<ScheduledTest>>(`/api/v1/tests?page=${page}&page_size=25`);
  const fmt = (v: string) => new Date(v).toLocaleString();
  return (
    <div className="stack">
      <PageHeader subtitle="Tests you created or may manage, with their state, schedule and audience." actions={<Link className="btn" to="/tests/new">Create Test</Link>} />
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(t) => t.plan_id}
            empty={<EmptyState title="No scheduled tests yet" hint="Create a test to see it here." />}
            columns={[
              { header: "Test", render: (t) => t.exam_id ? <Link to={`/exams/${t.exam_id}`}>{t.name}</Link> : t.name },
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
