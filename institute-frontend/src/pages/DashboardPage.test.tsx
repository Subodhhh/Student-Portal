import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const common = { "/api/v1/notifications/unread-count": { unread_count: 3 }, "/api/v1/providers": page([]) };
const PLATFORM = {
  role: "SUPER_ADMIN", upcoming_exams: 2, unread_notifications: 3, staff: 9, open_tickets: 1, unpaid_invoices: 0,
  providers_by_status: { ACTIVE: 4, INACTIVE: 1 }, branches_by_status: { ACTIVE: 6, INACTIVE: 1 }, students: 120, active_enrollments: 130,
  staff_by_role: { SUPER_ADMIN: 1, PROVIDER_ADMIN: 4, TEACHER: 5 }, subjects: 7, subjects_offered: 5, questions: 340,
  papers_by_status: { PUBLISHED: 11, VALIDATED: 2 }, composite_by_status: { OPEN: 1 }, exams_by_status: {}, recent_exams_30d: 6,
  upcoming_exam_list: [{ exam_id: "e1", name: "Weekly Physics", status: "SCHEDULED", start_at: "2026-11-01T10:00:00Z", provider_name: "Alpha", branch_name: "Main" }],
  security: { denied_events_24h: 2, failed_logins_24h: 0 },
  operations: { failed_deliveries: 0, pending_deliveries: 0, failed_generation_jobs: 0, failed_student_notifications: 0, unfinished_corrections: 0 },
};
afterEach(() => vi.unstubAllGlobals());

describe("dashboard", () => {
  it("gives the Super Admin a platform overview built only from API numbers, linking to the management pages", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["audit.read", "examplan.manage"]), "/api/v1/dashboard": PLATFORM,
      "/api/v1/audit-logs": (u: URL) => page(u.searchParams.get("outcome") === "DENIED"
        ? [{ audit_id: "x1", at: "2026-10-17T09:00:00Z", action: "authz.out_of_scope", target_type: "staff", outcome: "DENIED", reason: null, actor: null, actor_name: null, provider_name: null }]
        : [{ audit_id: "x2", at: "2026-10-17T08:00:00Z", action: "provider.create", target_type: "provider", outcome: "SUCCESS", reason: null, actor: "s1", actor_name: "Sam Admin", provider_name: "Alpha" }]) });
    renderApp("/");
    const card = (label: string) => screen.getByText(label).closest("a")!;
    await screen.findByText("Active providers");
    expect(within(card("Active providers")).getByText("4")).toBeTruthy();
    expect(within(card("Active providers")).getByText("1 inactive")).toBeTruthy();
    expect(card("Active providers").getAttribute("href")).toBe("/providers");
    expect(within(card("Enrolled students")).getByText("130 active enrollments")).toBeTruthy();
    expect(within(card("Platform subjects")).getByText("5 offered by providers")).toBeTruthy();
    expect(card("Unread notifications").getAttribute("href")).toBe("/notifications");
    expect(screen.getByText("Weekly Physics")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Teacher" }).getAttribute("href")).toBe("/staff/teachers");
    expect(await screen.findByText("authz.out_of_scope")).toBeTruthy();
    const activity = (await screen.findByRole("heading", { name: "Recent platform activity" })).closest("section")!;
    expect(within(activity).getByText("provider.create")).toBeTruthy();
    expect(within(activity).getByText("Sam Admin")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Create a test" }).getAttribute("href")).toBe("/exam-plans/new");
    expect(screen.queryByText("Needs attention:")).toBeNull();
  });

  it("shows an error with a retry instead of zeros when the dashboard cannot load", async () => {
    let calls = 0;
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", []), "/api/v1/dashboard": () => (++calls === 1 ? undefined : PLATFORM) });
    renderApp("/");
    expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
    expect(screen.queryByText("Active providers")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Active providers")).toBeTruthy();
  });

  it("keeps the scoped dashboard for other roles, without platform-wide numbers", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("BRANCH_ADMIN", []), "/api/v1/providers": page([]),
      "/api/v1/dashboard": { role: "BRANCH_ADMIN", upcoming_exams: 1, unread_notifications: 0, students: 40, batches: 3, pending_transfers: 0, teachers: 2 } });
    renderApp("/");
    expect(await screen.findByText("Enrolled students")).toBeTruthy();
    expect(screen.getByText("Active batches")).toBeTruthy();
    expect(screen.queryByText("Active providers")).toBeNull();
    expect(screen.queryByText("Staff by role")).toBeNull();
  });
});
