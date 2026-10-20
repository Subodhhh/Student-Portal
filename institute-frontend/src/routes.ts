import { matchPath } from "react-router-dom";
import type { Role } from "./auth/AuthContext";

// The single route-to-title table. The shared header shows the title of the current page, and the sidebar uses the same
// titles, so a page is called the same thing everywhere. Detail routes carry the title of the section they belong to.
type RouteTitle = { path: string; title: string | ((role?: Role) => string) };

export const ROUTE_TITLES: RouteTitle[] = [
  { path: "/", title: "Dashboard" },
  { path: "/providers", title: "Providers & Branches" },
  { path: "/providers/:providerId", title: "Providers & Branches" },
  { path: "/providers/:providerId/branches/:branchId", title: "Providers & Branches" },
  { path: "/staff", title: "Role Management" },
  { path: "/staff/:category", title: "Role Management" },
  { path: "/students", title: "Students & Enrollments" },
  { path: "/subjects", title: "Subjects" },
  { path: "/batches", title: "Batches" },
  { path: "/papers", title: "Question Bank & Papers" },
  { path: "/papers/:versionId", title: "Question Bank & Papers" },
  { path: "/tests/new", title: "Daily Test" },
  { path: "/tests/weekly", title: "Weekly Test" },
  { path: "/tests/weekly/new", title: "Weekly Test" },
  { path: "/tests/weekly/:seriesId", title: "Weekly Test" },
  { path: "/tests/global", title: "Global Tests" },
  { path: "/tests/global/:testId", title: "Global Tests" },
  { path: "/tests/global/:testId/schedule", title: "Global Tests" },
  { path: "/tests", title: "Scheduled Tests" },
  { path: "/exam-plans", title: "Exam Plans" },
  { path: "/exam-plans/new", title: "Exam Plans" },
  { path: "/composite", title: "Composite Tests" },
  { path: "/exams", title: "Exams & Results" },
  { path: "/exams/:examId", title: "Exams & Results" },
  { path: "/audit", title: "Audit Log" },
  { path: "/settings", title: (role) => (role === "SUPER_ADMIN" ? "Platform Settings" : "Settings") },
  { path: "/support", title: "Support" },
  { path: "/billing", title: "Billing" },
  { path: "/notifications", title: "Notifications" },
  { path: "/account", title: "My Account" },
  { path: "/system-health", title: "Service Health" },
];

export function routeTitle(path: string, role?: Role): string | undefined {
  const hit = ROUTE_TITLES.find((r) => r.path === path);
  if (!hit) return undefined;
  return typeof hit.title === "function" ? hit.title(role) : hit.title;
}

/** Title for the current location, matching nested routes (e.g. /providers/abc/branches/def -> Providers & Branches). */
export function pageTitle(pathname: string, role?: Role): string {
  const hit = ROUTE_TITLES.find((r) => matchPath({ path: r.path, end: true }, pathname));
  if (!hit) return "Institute Portal";
  return typeof hit.title === "function" ? hit.title(role) : hit.title;
}
