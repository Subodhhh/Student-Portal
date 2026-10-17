import type { Role } from "./AuthContext";

// Role data shared by Role Management, Providers & Branches and the dashboard. It mirrors the backend for display only;
// the API decides every permission.

export type Assignment = {
  assignment_id: string; role: Role; provider_id: string | null; branch_id: string | null; status: string;
  provider_name?: string | null; branch_name?: string | null;
};
export type Staff = {
  staff_id: string; staff_code: string; full_name: string; email: string; status: string; email_verified: boolean; assignments: Assignment[];
  created_at?: string; last_login_at?: string | null;
};

export const RANK: Record<Role, number> = { SUPER_ADMIN: 5, ADMIN: 4, PROVIDER_ADMIN: 3, BRANCH_ADMIN: 2, TEACHER: 1 };

// Roles each actor role may grant or remove (mirrors MANAGEABLE_ROLES in the backend; the server is authoritative).
export const GRANTABLE: Record<Role, Role[]> = {
  SUPER_ADMIN: ["SUPER_ADMIN", "ADMIN", "PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"],
  ADMIN: ["ADMIN", "PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"],
  PROVIDER_ADMIN: ["BRANCH_ADMIN", "TEACHER"], BRANCH_ADMIN: ["TEACHER"], TEACHER: [],
};

export const isGlobalRole = (role?: Role) => role === "SUPER_ADMIN" || role === "ADMIN";

export type Category = { slug: string; role: Role; title: string; description: string };

export const CATEGORIES: Category[] = [
  { slug: "super-admins", role: "SUPER_ADMIN", title: "Super Admin Management", description: "Platform owners with full access to every provider, setting and record." },
  { slug: "admins", role: "ADMIN", title: "Admin Management", description: "Platform administrators who run providers, staff and operations across the platform." },
  { slug: "provider-admins", role: "PROVIDER_ADMIN", title: "Provider Admin Management", description: "Run one provider and all of its branches." },
  { slug: "branch-admins", role: "BRANCH_ADMIN", title: "Branch Admin Management", description: "Run a single branch of a provider." },
  { slug: "teachers", role: "TEACHER", title: "Teacher Management", description: "Teach subjects for a provider, in the branches and batches they are assigned to." },
];

/** Categories a viewer can see: global roles see every account; scoped admins only the roles below or at their own level
 * (the backend limits the accounts in each list to their provider/branch either way). */
export function visibleCategories(role?: Role): Category[] {
  if (!role || role === "TEACHER") return [];
  if (isGlobalRole(role)) return CATEGORIES;
  return CATEGORIES.filter((c) => RANK[c.role] <= RANK[role]);
}

export const activeAssignments = (s: Staff) => s.assignments.filter((a) => a.status === "ACTIVE");
const unique = (xs: (string | null | undefined)[]) => [...new Set(xs.filter((x): x is string => !!x))];

/** Where an account works: provider and branch names of its active assignments (optionally for one role). */
export function scopeOf(s: Staff, role?: Role) {
  const rows = activeAssignments(s).filter((a) => !role || a.role === role);
  return { providers: unique(rows.map((a) => a.provider_name)), branches: unique(rows.map((a) => a.branch_name)) };
}


// Paper actions shown to `me` on a paper of `scope`. Platform (GLOBAL) papers are changed only by platform roles; the API enforces
// the same rule (and the provider/branch/teacher scope of provider papers), so this only avoids offering buttons that would fail.
export type PaperActions = { approve: boolean; publish: boolean; withdraw: boolean; correct: boolean; override: boolean };

export function paperActions(me: { primary_role: Role; permissions: string[]; assignments: { role: Role }[] } | null, scope?: string): PaperActions {
  const has = (p: string) => !!me?.permissions.includes(p);
  const platform = isGlobalRole(me?.primary_role) || !!me?.assignments.some((a) => isGlobalRole(a.role));
  const allowed = scope !== "GLOBAL" || platform;
  return {
    approve: allowed && has("paper.approve"), publish: allowed && has("paper.publish"), withdraw: allowed && has("paper.publish"),
    correct: allowed && has("paper.create"), override: me?.primary_role === "SUPER_ADMIN",
  };
}
