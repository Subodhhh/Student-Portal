import type { Role } from "./AuthContext";
import { routeTitle } from "../routes";

// Navigation is a UI convenience, not an authorization boundary.
// The backend remains responsible for authorizing every API request.
export type NavItem = {
  label: string;
  to: string;
  perm?: string;
  anyPerm?: string[];
};

const item = (to: string, rest: Omit<NavItem, "label" | "to"> = {}, role?: Role): NavItem => ({
  label: routeTitle(to, role) ?? to,
  to,
  ...rest,
});

// Super Admin: the platform-wide menu. Batches (a provider's student grouping) and Service Health (internal diagnostics,
// still reachable at /system-health) are deliberately not listed; Notifications are reached from the dashboard.
const SUPER_ADMIN_NAV: NavItem[] = [
  item("/"),
  item("/providers", { perm: "provider.read" }),
  item("/staff", { perm: "staff.read" }),
  item("/students", { perm: "enrollment.manage" }),
  item("/subjects", { perm: "subject.manage" }),
  item("/papers", { perm: "paper.read" }),
  item("/exam-plans", { perm: "examplan.manage" }),
  item("/composite", { perm: "examplan.manage" }),
  item("/exams", { perm: "exam.read" }),
  item("/audit", { perm: "audit.read" }),
  item("/settings", { perm: "settings.platform" }, "SUPER_ADMIN"),
  item("/support"),
  item("/billing", { perm: "billing.read" }),
  item("/account"),
];

// ADMIN, PROVIDER_ADMIN, BRANCH_ADMIN and TEACHER: the same pages and permissions as before, with Subjects and Batches
// as separate pages (the subject catalogue and a branch's student groups are different things).
const NAV: NavItem[] = [
  item("/"),
  item("/providers", { perm: "provider.read" }),
  item("/staff", { perm: "staff.read" }),
  item("/students", { perm: "enrollment.manage" }),
  item("/subjects", { anyPerm: ["subject.manage", "teaching.assign"] }),
  item("/batches", { perm: "batch.manage" }),
  item("/papers", { perm: "paper.read" }),
  item("/exam-plans", { perm: "examplan.manage" }),
  item("/composite", { perm: "examplan.manage" }),
  item("/exams", { perm: "exam.read" }),
  item("/notifications"),
  item("/support"),
  item("/billing", { perm: "billing.read" }),
  item("/settings", { anyPerm: ["settings.platform", "settings.provider", "settings.branch"] }),
  item("/audit", { perm: "audit.read" }),
  item("/account"),
];

export function visibleNav(
  permissions: string[],
  role?: Role,
): NavItem[] {
  const items = role === "SUPER_ADMIN" ? SUPER_ADMIN_NAV : NAV;

  return items.filter(
    (item) =>
      (!item.perm && !item.anyPerm) ||
      (!!item.perm && permissions.includes(item.perm)) ||
      (!!item.anyPerm &&
        item.anyPerm.some((permission) => permissions.includes(permission))),
  );
}

export const ROLE_LABEL: Record<Role, string> = {
  SUPER_ADMIN: "Super Admin",
  ADMIN: "Admin",
  PROVIDER_ADMIN: "Provider Admin",
  BRANCH_ADMIN: "Branch Admin",
  TEACHER: "Teacher",
};
