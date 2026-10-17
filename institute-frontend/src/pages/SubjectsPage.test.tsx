import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const SA = me("SUPER_ADMIN", ["subject.manage", "provider.read", "batch.manage", "teaching.assign"]);
const PHY = { subject_id: "s1", code: "PHYSICS", name: "Physics", status: "ACTIVE" };
const CHE = { subject_id: "s2", code: "CHEMISTRY", name: "Chemistry", status: "ACTIVE" };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]) };
afterEach(() => vi.unstubAllGlobals());

describe("Subjects: platform catalogue (Super Admin)", () => {
  it("lists catalogue subjects with search and status, and has no batch or teaching controls", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": SA, "/api/v1/subjects": (u: URL) => page(u.searchParams.get("q") === "chem" ? [CHE] : [PHY, CHE]) });
    renderApp("/subjects");
    expect(await screen.findByText("Physics")).toBeTruthy();
    expect(screen.getByText("CHEMISTRY")).toBeTruthy();
    expect(screen.queryByText(/batch/i)).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search by name or code" }), { target: { value: "chem" } });
    await waitFor(() => expect(screen.queryByText("Physics")).toBeNull());
    expect(calls.filter((c) => c.path === "/api/v1/subjects").at(-1)?.search.get("status")).toBe("ACTIVE");
  });

  it("adds a subject", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": SA, "/api/v1/subjects": page([]), "POST /api/v1/subjects": { ...PHY, subject_id: "new" } });
    renderApp("/subjects");
    fireEvent.click((await screen.findAllByRole("button", { name: "Add subject" }))[0]);
    const dialog = await screen.findByRole("dialog", { name: "Add subject" });
    fireEvent.change(within(dialog).getByLabelText("Code"), { target: { value: "physics" } });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Physics" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add subject" }));
    expect(await screen.findByText("Physics was added to the catalogue.")).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ code: "PHYSICS", name: "Physics" });
  });

  it("renames a subject without changing its code", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": SA, "/api/v1/subjects": page([PHY]), "PATCH /api/v1/subjects/s1": { ...PHY, name: "Physics (JEE)" } });
    renderApp("/subjects");
    fireEvent.click(await screen.findByRole("button", { name: "Edit Physics" }));
    const dialog = await screen.findByRole("dialog");
    expect((within(dialog).getByLabelText("Code") as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Physics (JEE)" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Physics (JEE) was saved.")).toBeTruthy();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Physics (JEE)" });
  });

  it("deactivates instead of deleting, after confirmation with a reason", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": SA, "/api/v1/subjects": page([PHY]), "PATCH /api/v1/subjects/s1": { ...PHY, status: "INACTIVE" } });
    renderApp("/subjects");
    expect(await screen.findByText("Physics")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Deactivate Physics" }));
    const dialog = await screen.findByRole("dialog", { name: "Deactivate Physics" });
    expect(within(dialog).getByText(/Nothing is deleted/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Reason/), { target: { value: "Merged into Science" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));
    expect(await screen.findByText("Physics is now inactive.")).toBeTruthy();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "INACTIVE", reason: "Merged into Science" });
  });
});

describe("Subjects: provider staff", () => {
  it("shows the provider's offered subjects from the catalogue, without catalogue management", async () => {
    const calls = stubApi({
      ...common,
      "/api/v1/auth/me": me("PROVIDER_ADMIN", ["subject.manage", "teaching.assign"]),
      "/api/v1/subjects": page([PHY, CHE]),
      "/api/v1/providers/p1/subjects": [PHY],
      "PUT /api/v1/providers/p1/subjects/s2": CHE,
    });
    renderApp("/subjects");
    expect(await screen.findByRole("tab", { name: "Offered subjects" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Teaching assignments" })).toBeTruthy();
    expect(await screen.findByText("Physics")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add subject" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Deactivate/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Offer subject" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.path === "/api/v1/providers/p1/subjects/s2")).toBe(true));
  });

  it("assigns teachers of this provider only, in the chosen branch", async () => {
    const calls = stubApi({
      ...common,
      "/api/v1/auth/me": me("BRANCH_ADMIN", ["teaching.assign"]),
      "/api/v1/providers/p1/subjects": [PHY],
      "/api/v1/teaching": page([]),
      "/api/v1/staff": page([{ staff_id: "t1", staff_code: "STF000010", full_name: "Tara Teacher", email: "t@x.io", status: "ACTIVE", email_verified: true, assignments: [] }]),
      "/api/v1/branches/b1/batches": page([]),
      "POST /api/v1/teaching": { teaching_id: "x" },
    });
    renderApp("/subjects");
    const teacher = await screen.findByLabelText("Teacher");
    await screen.findByRole("option", { name: "Tara Teacher (STF000010)" });
    fireEvent.change(teacher, { target: { value: "t1" } });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "s1" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ staff_id: "t1", branch_id: "b1", subject_id: "s1", batch_id: null }));
    const staffCall = calls.find((c) => c.path === "/api/v1/staff")!;
    expect(staffCall.search.get("provider_id")).toBe("p1");
    expect(staffCall.search.get("role")).toBe("TEACHER");
  });
});
