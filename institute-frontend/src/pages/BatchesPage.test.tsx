import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const PERMS = ["batch.manage", "enrollment.manage"];
const MORNING = { batch_id: "bt1", branch_id: "b1", name: "Morning", description: null, status: "ACTIVE", start_date: null, end_date: null, student_count: 2 };
const OLD = { ...MORNING, batch_id: "bt2", name: "Old group", status: "INACTIVE" };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/auth/me": me("BRANCH_ADMIN", PERMS),
  "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]),
  "/api/v1/branches/b1/batches": (u: URL) => page(u.searchParams.get("status") === "INACTIVE" ? [OLD] : [MORNING]),
  "/api/v1/batches/bt1/subjects": [{ subject_id: "s", code: "PHY", name: "Physics" }] };

afterEach(() => vi.unstubAllGlobals());

describe("Batches", () => {
  it("offers the full set of actions, and shows the student count", async () => {
    stubApi(common);
    renderApp("/batches");
    expect(await screen.findByRole("cell", { name: "Morning" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "2" })).toBeTruthy();
    for (const name of ["View", "Edit", "Subjects", "Class defaults", "Add students", "Delete"]) expect(screen.getByRole("button", { name })).toBeTruthy();
  });

  it("deletes by deactivating, and reactivates from the inactive list", async () => {
    const calls = stubApi({ ...common, "PATCH /api/v1/batches/bt1": MORNING, "PATCH /api/v1/batches/bt2": OLD });
    renderApp("/batches");
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete batch" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "INACTIVE" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "INACTIVE" } });
    fireEvent.click(await screen.findByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").at(-1)).toMatchObject({ path: "/api/v1/batches/bt2", body: { status: "ACTIVE" } }));
    expect(screen.queryByRole("button", { name: "Add students" })).toBeNull();       // nothing new on an inactive batch
  });

  it("adds selected students and reports who was already there", async () => {
    const calls = stubApi({ ...common, "/api/v1/branches/b1/enrollments": page([{ enrollment_id: "e1", student_id: "STU000001", student_name: "Asha Rao" }, { enrollment_id: "e2", student_id: "STU000002", student_name: "Ben Roy" }]),
      "POST /api/v1/batches/bt1/members": { added: ["e1"], already_members: ["e2"], rejected: [] } });
    renderApp("/batches");
    fireEvent.click(await screen.findByRole("button", { name: "Add students" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select STU000001" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select STU000002" }));
    fireEvent.click(screen.getByRole("button", { name: "Add 2 students" }));
    expect(await screen.findByText(/Added 1\. 1 already in this batch\./)).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ enrollment_ids: ["e1", "e2"] });
    expect(calls.find((c) => c.path === "/api/v1/branches/b1/enrollments")?.search.get("not_in_batch")).toBe("bt1");
  });
});
