import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubApi } from "../test/stubApi";
import { IntegrityCard } from "./ExamIntegrity";

const A1 = "11111111-1111-4111-8111-111111111111";
const row = (over: Record<string, unknown> = {}) => ({ student_id: "STU000101", name: "Asha K", attempt_id: A1, status: "AUTO_SUBMITTED",
  submit_reason: "WARNING_THRESHOLD", review_status: "UNDER_REVIEW", warnings: 3, retake_of_attempt_id: null,
  review: { status: "OPEN", student_message: "A system update prompt took focus." }, can_decide: true, ...over });
const detail = (over: Record<string, unknown> = {}) => ({ ...row(over), threshold: 3, started_at: "2026-10-25T10:00:00Z", submitted_at: "2026-10-25T10:20:00Z",
  events: [{ kind: "TAB_HIDDEN", received_at: "2026-10-25T10:05:00Z", counted: true, warning_no: 1 },
           { kind: "WINDOW_BLUR", received_at: "2026-10-25T10:05:01Z", counted: false, warning_no: null }], retake: null });

afterEach(() => vi.unstubAllGlobals());

describe("Exam integrity", () => {
  it("lists the caller's flagged attempts and lets an authorized admin authorize a retake with a reason", async () => {
    const calls = stubApi({ "/api/v1/exams/e1/integrity": { items: [row()] }, [`/api/v1/exams/e1/integrity/${A1}`]: detail(),
      [`POST /api/v1/exams/e1/integrity/${A1}/decision`]: { status: "RETAKE_AUTHORIZED" } });
    render(<IntegrityCard examId="e1" />);
    expect(screen.getByText(/not proof of cheating/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Load warnings" }));
    expect(await screen.findByText("Automatic (warning limit)")).toBeTruthy();
    expect(screen.getByText("Student asked for a review")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Investigate" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Warning 1")).toBeTruthy();
    expect(within(dialog).getByText("Same action (not counted)")).toBeTruthy();
    expect(within(dialog).getByText(/one new, separate attempt/)).toBeTruthy();
    const authorize = within(dialog).getByRole("button", { name: "Authorize a retake" }) as HTMLButtonElement;
    expect(authorize.disabled).toBe(true);                                         // a reason is required
    fireEvent.change(within(dialog).getByLabelText("Reason (required, recorded)"), { target: { value: "Verified an OS update prompt." } });
    fireEvent.click(authorize);
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "RETAKE", reason: "Verified an OS update prompt." });
  });

  it("shows no decision controls to staff who may only investigate", async () => {
    stubApi({ "/api/v1/exams/e1/integrity": { items: [row({ can_decide: false })] }, [`/api/v1/exams/e1/integrity/${A1}`]: detail({ can_decide: false }) });
    render(<IntegrityCard examId="e1" />);
    fireEvent.click(screen.getByRole("button", { name: "Load warnings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Investigate" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Only the Platform Admin or the student's Provider Admin/)).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "Authorize a retake" })).toBeNull();
  });
});
