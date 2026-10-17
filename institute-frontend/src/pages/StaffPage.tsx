import { Link, Navigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { CATEGORIES, visibleCategories, type Category } from "../auth/roles";
import { StaffList } from "../components/staff";
import { Alert, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";

/** Role Management: one card per role category, each with its live account count. */
export default function RoleManagementPage() {
  const { me } = useAuth();
  const categories = visibleCategories(me?.primary_role);
  return (
    <>
      <PageHeader subtitle="Staff accounts grouped by role. Open a category to search, inspect and manage its accounts." />
      {categories.length === 0 ? <Alert kind="info">Your role does not manage staff accounts.</Alert> : (
        <div className="grid category-grid">
          {categories.map((c) => <CategoryCard key={c.slug} category={c} />)}
        </div>
      )}
    </>
  );
}

function CategoryCard({ category }: { category: Category }) {
  // page_size=1: only the total is needed. The count is of accounts holding an active assignment of this role in the
  // viewer's scope, exactly what the category list shows.
  const count = useApi<Page<unknown>>(`/api/v1/staff?role=${category.role}&page_size=1`);
  return (
    <Link to={`/staff/${category.slug}`} className="category-card">
      <span className="category-card-title">{category.title}</span>
      <span className="category-card-count" aria-label={`${count.data?.total ?? 0} accounts`}>
        {count.error ? "—" : count.data ? count.data.total : "…"}
        <span className="muted small"> {count.data?.total === 1 ? "account" : "accounts"}</span>
      </span>
      <span className="muted small">{category.description}</span>
    </Link>
  );
}

export function RoleCategoryPage() {
  const { category = "" } = useParams();
  const { me } = useAuth();
  const c = CATEGORIES.find((x) => x.slug === category);
  if (!c) return <Navigate to="/staff" replace />;
  if (!visibleCategories(me?.primary_role).includes(c)) return <Navigate to="/forbidden" replace />;
  const providerScoped = c.role === "PROVIDER_ADMIN" || c.role === "BRANCH_ADMIN" || c.role === "TEACHER";
  return (
    <>
      <PageHeader crumbs={[<Link key="rm" to="/staff">Role Management</Link>, c.title]} title={c.title} subtitle={c.description} />
      <StaffList key={c.role} role={c.role} addRole={c.role} providerFilter={providerScoped} />
      <p className="muted small">Accounts listed hold an active {ROLE_LABEL[c.role]} role. One person can hold several roles and then appears in each category.</p>
    </>
  );
}
