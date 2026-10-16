
import type { Role } from "./AuthContext";

// Navigation is a UI convenience, not an authorization boundary.
// The backend remains responsible for authorizing every API request.
export type NavItem = {
  label: string;
  to: string;
  perm?: string;
  anyPerm?: string[];
};

const SUPER_ADMIN_NAV: NavItem[] = [
  { label: "Dashboard", to: "/" },
  { label: "Providers & Branches", to: "/providers", perm: "provider.read" },
  { label: "Role Management", to: "/staff", perm: "staff.read" },
  { label: "Students & Enrollments", to: "/students", perm: "enrollment.manage" },
  { label: "Subjects", to: "/academics", perm: "subject.manage" },
  { label: "Question Bank & Papers", to: "/papers", perm: "paper.read" },
  { label: "Exam Plans", to: "/exam-plans", perm: "examplan.manage" },
  { label: "Composite Tests", to: "/composite", perm: "examplan.manage" },
  { label: "Exams & Results", to: "/exams", perm: "exam.read" },
  { label: "Service Health", to: "/system-health", perm: "settings.platform" },
  { label: "Notifications", to: "/notifications" },
  { label: "Audit Log", to: "/audit", perm: "audit.read" },
  { label: "Platform Settings", to: "/settings", perm: "settings.platform" },
  { label: "Support", to: "/support" },
  { label: "Billing", to: "/billing", perm: "billing.read" },
];

// Existing navigation for ADMIN, PROVIDER_ADMIN, BRANCH_ADMIN and TEACHER.
const NAV: NavItem[] = [
  { label: "Dashboard", to: "/" },
  { label: "Providers", to: "/providers", perm: "provider.read" },
  { label: "Staff", to: "/staff", perm: "staff.read" },
  { label: "Students & enrollments", to: "/students", perm: "enrollment.manage" },
  {
    label: "Subjects & batches",
    to: "/academics",
    anyPerm: ["batch.manage", "subject.manage", "teaching.assign"],
  },
  { label: "Question bank & papers", to: "/papers", perm: "paper.read" },
  { label: "Exam plans", to: "/exam-plans", perm: "examplan.manage" },
  { label: "Composite tests", to: "/composite", perm: "examplan.manage" },
  { label: "Exams & results", to: "/exams", perm: "exam.read" },
  { label: "Notifications", to: "/notifications" },
  { label: "Support", to: "/support" },
  { label: "Billing", to: "/billing", perm: "billing.read" },
  {
    label: "Settings",
    to: "/settings",
    anyPerm: ["settings.platform", "settings.provider", "settings.branch"],
  },
  { label: "Audit log", to: "/audit", perm: "audit.read" },
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
