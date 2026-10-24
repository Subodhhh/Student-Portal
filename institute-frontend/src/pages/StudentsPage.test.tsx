import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const PHY = { subject_id: "phy", name: "Physics" };
const CHEM = { subject_id: "chem", name: "Chemistry" };
const E1 = { enrollment_id: "e1", student_id: "STU000001", student_name: "Riya Patil", status: "ACTIVE", batch_ids: ["A"], batch_subjects: { A: ["phy"] }, enrolled_at: "2026-10-01" };
const E2 = { enrollment_id: "e2", student_id: "STU000002", student_name: "Aman Shah", status: "ACTIVE", batch_ids: ["A"], batch_subjects: {}, enrolled_at: "2026-10-01" };
afterEach(() => vi.unstubAllGlobals());

const stub = () => stubApi({
  "/api/v1/auth/me": me("PROVIDER_ADMIN", ["enrollment.manage"]), "/api/v1/notifications/unread-count": { unread_count: 0 },
  "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]),
  "/api/v1/branches/b1/enrollments": page([E1, E2]), "/api/v1/branches/b1/batches": page([{ batch_id: "A", name: "NEET 2027", status: "ACTIVE" }]),
  "/api/v1/providers/p1/subjects": [PHY, CHEM], "/api/v1/batches/A/subjects": [PHY, CHEM],
  "PUT /api/v1/enrollments/e2/batches/A": {}, "POST /api/v1/branches/b1/enrollments": { ...E2, enrollment_id: "e9" },
  "POST /api/v1/enrollments/e2/password-setup-link": { student_id: "STU000002", expires_at: "2026-10-24T12:00:00+00:00" },
});

describe("Subject-wise enrollment", () => {
  it("shows the chosen subjects and changes them per batch membership (all subjects = no selection)", async () => {
    const calls = stub();
    renderApp("/students?tab=branch");
    expect(await screen.findByText("NEET 2027 (Physics)")).toBeTruthy();         // e1 takes Physics only
    const row = screen.getByText("Aman Shah").closest("tr")!;
    expect(within(row).getByText("NEET 2027")).toBeTruthy();                      // e2: every subject
    fireEvent.click(within(row).getByRole("button", { name: "Manage" }));
    const group = await screen.findByRole("group", { name: "Subjects in NEET 2027" });
    fireEvent.click(within(group).getByLabelText("Chemistry"));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ subject_ids: ["phy"] }]));
    await waitFor(() => expect((within(group).getByLabelText("Physics") as HTMLInputElement).disabled).toBe(true));   // the last one stays
    fireEvent.click(within(group).getByLabelText("Chemistry"));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ subject_ids: ["phy"] }, { subject_ids: null }]));
    expect(calls.filter((c) => c.method === "PUT").every((c) => c.path === "/api/v1/enrollments/e2/batches/A")).toBe(true);
  });

  it("sends the subjects chosen under a batch when enrolling, and nothing for a batch taking every subject", async () => {
    const calls = stub();
    renderApp("/students?tab=branch");
    fireEvent.click(await screen.findByRole("button", { name: "Enroll student" }));
    fireEvent.click(await screen.findByRole("button", { name: "New student" }));
    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Zoya" } });
    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Khan" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "z@example.com" } });
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByLabelText("NEET 2027"));
    const group = await within(dialog).findByRole("group", { name: "Subjects in NEET 2027" });
    fireEvent.click(within(group).getByLabelText("Physics"));
    fireEvent.change(within(dialog).getByLabelText(/Class \/ level/), { target: { value: "11TH" } });
    fireEvent.change(within(dialog).getByLabelText(/Academic year/), { target: { value: "2026-2027" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Enroll" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const body = calls.find((c) => c.method === "POST")!.body as Record<string, unknown>;
    expect(body.batch_ids).toEqual(["A"]);
    expect(body.batch_subjects).toEqual([{ batch_id: "A", subject_ids: ["chem"] }]);
  });

  it("sends a password setup link for the enrollment from the Manage dialog", async () => {
    const calls = stub();
    renderApp("/students?tab=branch");
    fireEvent.click(within((await screen.findByText("Aman Shah")).closest("tr")!).getByRole("button", { name: "Manage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Send password setup link" }));
    expect(await screen.findByText(/Password setup link sent to the student's email/)).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/v1/enrollments/e2/password-setup-link"]);
  });
});
