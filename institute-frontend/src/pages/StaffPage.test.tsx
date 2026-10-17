import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const COUNTS: Record<string, number> = { SUPER_ADMIN: 1, ADMIN: 2, PROVIDER_ADMIN: 3, BRANCH_ADMIN: 4, TEACHER: 12 };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }, { provider_id: "p2", name: "Beta", status: "ACTIVE", email: "b@x.io" }]),
  "/api/v1/providers/p1/branches": page([]) };
const staff = (id: string, role: string, extra: Record<string, unknown> = {}) => ({
  staff_id: id, staff_code: `STF0000${id}`, full_name: `Person ${id}`, email: `${id}@x.io`, status: "ACTIVE", email_verified: true,
  assignments: [{ assignment_id: `a${id}`, role, provider_id: role === "SUPER_ADMIN" || role === "ADMIN" ? null : "p1", branch_id: role === "BRANCH_ADMIN" ? "b1" : null, status: "ACTIVE",
    provider_name: role === "SUPER_ADMIN" || role === "ADMIN" ? null : "Alpha", branch_name: role === "BRANCH_ADMIN" ? "Main" : null }],
  ...extra,
});
afterEach(() => vi.unstubAllGlobals());

describe("Role Management", () => {
  it("shows the five categories to the Super Admin with live counts from the API", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["staff.read", "staff.create", "staff.assign_role"]),
      "/api/v1/staff": (u: URL) => ({ items: [], total: COUNTS[u.searchParams.get("role")!], page: 1, page_size: 1 }) });
    renderApp("/staff");
    for (const [title, count] of [["Super Admin Management", 1], ["Admin Management", 2], ["Provider Admin Management", 3], ["Branch Admin Management", 4], ["Teacher Management", 12]] as const) {
      const card = (await screen.findByText(title)).closest("a")!;
      await within(card).findByText(String(count));
    }
    expect(screen.queryByRole("table")).toBeNull();     // no flat staff table on the landing page
    expect(calls.filter((c) => c.path === "/api/v1/staff").every((c) => c.search.get("page_size") === "1")).toBe(true);
  });

  it("shows a Provider Admin only the categories at or below their level", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["staff.read"]), "/api/v1/staff": page([]) });
    renderApp("/staff");
    expect(await screen.findByText("Provider Admin Management")).toBeTruthy();
    expect(screen.getByText("Branch Admin Management")).toBeTruthy();
    expect(screen.getByText("Teacher Management")).toBeTruthy();
    expect(screen.queryByText("Super Admin Management")).toBeNull();
    expect(screen.queryByText("Admin Management")).toBeNull();
  });

  it("refuses a category the viewer cannot manage", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("BRANCH_ADMIN", ["staff.read"]), "/api/v1/staff": page([]) });
    renderApp("/staff/admins");
    expect(await screen.findByText("Access denied")).toBeTruthy();
  });

  it("opens a category as a searchable, filtered list of that role's accounts", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["staff.read", "staff.create", "staff.assign_role"]),
      "/api/v1/staff": page([staff("11", "TEACHER"), staff("12", "TEACHER", { status: "INACTIVE" })]) });
    renderApp("/staff/teachers");
    expect(await screen.findByText("Person 11")).toBeTruthy();
    const table = screen.getByRole("table");
    for (const h of ["Staff ID", "Name", "Email", "Role", "Provider", "Branch", "Status", "Actions"]) expect(within(table).getByRole("columnheader", { name: h })).toBeTruthy();
    expect(within(table).getAllByText("Alpha")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Add Teacher" })).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "tara" } });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "p2" } });
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/api/v1/staff").at(-1)!;
      expect(last.search.get("role")).toBe("TEACHER");
      expect(last.search.get("q")).toBe("tara");
      expect(last.search.get("provider_id")).toBe("p2");
    });
  });

  it("offers only the actions the backend allows: a Super Admin may deactivate an Admin", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["staff.read", "staff.assign_role"]), "/api/v1/staff": page([staff("21", "ADMIN")]) });
    renderApp("/staff/admins");
    fireEvent.click(await screen.findByRole("button", { name: "View Person 21" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Deactivate account" })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Remove role" })).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: /suspend/i })).toBeNull();     // no such backend operation
  });

  it("an Admin cannot act on a Super Admin, and a Provider Admin cannot deactivate accounts", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("ADMIN", ["staff.read", "staff.assign_role"]), "/api/v1/staff": page([staff("31", "SUPER_ADMIN")]) });
    renderApp("/staff/super-admins");
    fireEvent.click(await screen.findByRole("button", { name: "View Person 31" }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: "Deactivate account" })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Remove role" })).toBeNull();
    vi.unstubAllGlobals();

    stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["staff.read", "staff.assign_role"]), "/api/v1/staff": page([staff("41", "TEACHER")]) });
    renderApp("/staff/teachers");
    fireEvent.click(await screen.findByRole("button", { name: "View Person 41" }));
    dialog = (await screen.findAllByRole("dialog")).at(-1)!;
    expect(within(dialog).queryByRole("button", { name: "Deactivate account" })).toBeNull();
    expect(within(dialog).getByRole("button", { name: "Remove role" })).toBeTruthy();
  });

  it("removing a role asks for confirmation and a reason", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["staff.read", "staff.assign_role"]), "/api/v1/staff": page([staff("51", "TEACHER")]),
      "DELETE /api/v1/staff/51/assignments/a51": { message: "ok" } });
    renderApp("/staff/teachers");
    fireEvent.click(await screen.findByRole("button", { name: "View Person 51" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove role" }));
    const confirm = await screen.findByRole("dialog", { name: "Remove Teacher role" });
    fireEvent.change(within(confirm).getByLabelText(/Reason/), { target: { value: "Left the institute" } });
    fireEvent.click(within(confirm).getByRole("button", { name: "Remove role" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ reason: "Left the institute" }));
  });
});
