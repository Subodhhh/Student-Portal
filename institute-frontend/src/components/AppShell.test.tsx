import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { me, page, stubApi } from "../test/stubApi";
import { renderApp } from "../test/renderApp";

const PROVIDERS = [{ provider_id: "p1", name: "Alpha Institute", status: "ACTIVE", email: "a@x.io" }, { provider_id: "p2", name: "Beta Classes", status: "ACTIVE", email: "b@x.io" }];
const BRANCHES = [{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }, { branch_id: "b2", provider_id: "p1", name: "North", city: null, status: "ACTIVE" }];
const SA_PERMS = ["provider.read", "staff.read", "enrollment.manage", "subject.manage", "batch.manage", "paper.read", "examplan.manage", "exam.read", "audit.read", "settings.platform", "billing.read"];

function base(role: string, permissions: string[], extra: Record<string, unknown> = {}) {
  return stubApi({
    "/api/v1/auth/me": me(role, permissions),
    "/api/v1/notifications/unread-count": { unread_count: 0 },
    "/api/v1/providers": page(PROVIDERS),
    "/api/v1/providers/p1/branches": page(BRANCHES),
    ...extra,
  });
}
afterEach(() => vi.unstubAllGlobals());

describe("shared header", () => {
  it("shows the current page title and the signed-in person, and no global provider/branch selectors", async () => {
    base("SUPER_ADMIN", SA_PERMS, { "/api/v1/subjects": page([]) });
    renderApp("/subjects");
    const header = (await screen.findByRole("heading", { level: 1, name: "Subjects" })).closest("header")!;
    expect(within(header).getByText("Sam Admin")).toBeTruthy();
    expect(within(header).getByText(/STF000001 · Super Admin/)).toBeTruthy();
    expect(within(header).getByRole("button", { name: "Sign out" })).toBeTruthy();
    expect(within(header).queryByRole("combobox")).toBeNull();
    // exactly one h1 on the page: the page body does not repeat the title
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it.each([
    ["/staff", "Role Management"],
    ["/staff/teachers", "Role Management"],
    ["/providers", "Providers & Branches"],
    ["/students", "Students & Enrollments"],
  ])("titles %s as %s", async (path, title) => {
    base("SUPER_ADMIN", SA_PERMS, {
      "/api/v1/staff": page([]),
      "/api/v1/branches/b1/enrollments": page([]),
      "/api/v1/branches/b1/batches": page([]),
    });
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: title })).toBeTruthy();
  });

  it("shows the same header to a teacher", async () => {
    base("TEACHER", ["paper.read", "exam.read"], { "/api/v1/branches/b1/papers": page([]), "/api/v1/providers/p1/subjects": [] });
    renderApp("/papers");
    expect(await screen.findByRole("heading", { level: 1, name: "Question Bank & Papers" })).toBeTruthy();
    expect(screen.getByText(/STF000001 · Teacher/)).toBeTruthy();
  });

  it("gives the Super Admin the platform sidebar without Service Health, Batches or Notifications", async () => {
    base("SUPER_ADMIN", SA_PERMS, { "/api/v1/dashboard": { role: "SUPER_ADMIN", upcoming_exams: 0, unread_notifications: 0 } });
    renderApp("/");
    const nav = await screen.findByRole("complementary", { name: "Primary" });
    const links = within(nav).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(["Dashboard", "Providers & Branches", "Role Management", "Students & Enrollments", "Subjects", "Question Bank & Papers",
      "Global Tests", "Scheduled Tests", "Exam Plans", "Exams & Results", "Audit Log", "Platform Settings", "Support", "Billing", "My Account"]);
  });
});

describe("in-page provider and branch context", () => {
  it("lets a branch-scoped page choose its provider and branch, and asks the API for that branch", async () => {
    const calls = base("SUPER_ADMIN", SA_PERMS, {
      "/api/v1/branches/b1/enrollments": page([]),
      "/api/v1/branches/b2/enrollments": page([{ enrollment_id: "e1", student_id: "STU000009", student_name: "Riya North", status: "ACTIVE", batch_ids: [], enrolled_at: "2026-10-01" }]),
      "/api/v1/branches/b1/batches": page([]),
      "/api/v1/branches/b2/batches": page([]),
    });
    renderApp("/students?tab=branch");
    const bar = await screen.findByRole("group", { name: "Working in" });
    const [provider, branch] = within(bar).getAllByRole("combobox") as HTMLSelectElement[];
    expect(provider.value).toBe("p1");
    expect(await screen.findByText("No students enrolled")).toBeTruthy();
    branch.value = "b2";
    branch.dispatchEvent(new Event("change", { bubbles: true }));
    expect(await screen.findByText("Riya North")).toBeTruthy();
    expect(calls.some((c) => c.path === "/api/v1/branches/b2/enrollments")).toBe(true);
  });
});
