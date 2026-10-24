import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const ALPHA = { provider_id: "p1", name: "Alpha Institute", status: "ACTIVE", email: "a@x.io" };
const MAIN = { branch_id: "b1", provider_id: "p1", name: "Main", city: "Pune", status: "ACTIVE" };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([ALPHA]), "/api/v1/providers/p1/branches": page([MAIN]),
  "/api/v1/providers/p1": ALPHA, "/api/v1/branches/b1": MAIN };
const PA = { staff_id: "s1", staff_code: "STF000101", full_name: "Priya Admin", email: "p@x.io", status: "ACTIVE", email_verified: true,
  assignments: [{ assignment_id: "a1", role: "PROVIDER_ADMIN", provider_id: "p1", branch_id: null, status: "ACTIVE", provider_name: "Alpha Institute", branch_name: null }] };
afterEach(() => vi.unstubAllGlobals());

describe("Providers & Branches", () => {
  it("shows a provider's branches and its staff through the provider filter of the staff API", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["provider.read", "staff.read", "branch.create"]), "/api/v1/staff": page([PA]), "/api/v1/subjects": page([]), "/api/v1/providers/p1/subjects": [] });
    renderApp("/providers/p1");
    expect(await screen.findByRole("heading", { level: 2, name: "Alpha Institute" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Branches" }));
    expect(await screen.findByRole("link", { name: "Main" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Staff" }));
    expect(await screen.findByText("Priya Admin")).toBeTruthy();
    expect(calls.find((c) => c.path === "/api/v1/staff")!.search.get("provider_id")).toBe("p1");
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "BRANCH_ADMIN" } });
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/v1/staff").at(-1)!.search.get("role")).toBe("BRANCH_ADMIN"));
  });

  it("shows a branch's staff through the branch filter, and its teaching assignments", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["provider.read", "staff.read", "teaching.assign"]), "/api/v1/staff": page([]),
      "/api/v1/teaching": page([]), "/api/v1/providers/p1/subjects": [], "/api/v1/branches/b1/batches": page([]) });
    renderApp("/providers/p1/branches/b1");
    expect(await screen.findByRole("heading", { level: 2, name: "Main" })).toBeTruthy();
    await waitFor(() => expect(calls.find((c) => c.path === "/api/v1/staff")?.search.get("branch_id")).toBe("b1"));
    fireEvent.click(screen.getByRole("tab", { name: "Teaching assignments" }));
    expect(await screen.findByText("No teaching assignments in this branch")).toBeTruthy();
  });

  it("manages a branch's batches from its Batches tab, by this branch's ID", async () => {
    let batches = [{ batch_id: "bt1", name: "NEET 2027", status: "ACTIVE", start_date: null, end_date: null }];
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["provider.read", "staff.read", "batch.manage"]), "/api/v1/staff": page([]),
      "/api/v1/teaching": page([]), "/api/v1/providers/p1/subjects": [],
      "/api/v1/branches/b1/batches": () => page(batches),
      "POST /api/v1/branches/b1/batches": (_u: URL, init: RequestInit) => {
        const body = JSON.parse(String(init.body));
        batches = [...batches, { batch_id: "bt2", name: body.name, status: "ACTIVE", start_date: null, end_date: null }];
        return batches[1];
      } });
    renderApp("/providers/p1/branches/b1");
    fireEvent.click(await screen.findByRole("tab", { name: "Batches" }));
    expect(await screen.findByText("NEET 2027")).toBeTruthy();
    expect(calls.some((c) => c.method === "GET" && c.path === "/api/v1/branches/b1/batches")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "New batch" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "JEE 2028" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("JEE 2028")).toBeTruthy();
    const post = calls.filter((c) => c.method === "POST");
    expect(post.map((c) => c.path)).toEqual(["/api/v1/branches/b1/batches"]);              // created in THIS branch, by its ID
    expect(post[0].body).toEqual({ name: "JEE 2028", start_date: null, end_date: null,                       // no Class defaults chosen
      default_class_level: null, default_academic_year: null, default_other_description: null });
    expect(calls.some((c) => /\/branches\/(?!b1\/)[^/]+\/batches/.test(c.path))).toBe(false); // no other branch's batches
  });

  it("shows no Batches tab without batch.manage", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["provider.read", "teaching.assign"]), "/api/v1/teaching": page([]),
      "/api/v1/providers/p1/subjects": [], "/api/v1/branches/b1/batches": page([]) });
    renderApp("/providers/p1/branches/b1");
    expect(await screen.findByRole("tab", { name: "Teaching assignments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Batches" })).toBeNull();
  });

  it("does not show a branch that belongs to another provider under this provider", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["provider.read", "staff.read"]), "/api/v1/branches/b9": { ...MAIN, branch_id: "b9", provider_id: "p2" } });
    renderApp("/providers/p1/branches/b9");
    expect(await screen.findByText("Branch not found.")).toBeTruthy();
  });
});
