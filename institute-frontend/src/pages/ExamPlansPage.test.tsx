import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]) };
const test = (status: string, extra: Record<string, unknown> = {}) => ({ test_id: "gt1", name: "Daily Physics #7", description: null, exam_type: "NEET", target_class: "Class 12",
  duration_minutes: 30, status, derived_from_test_id: null, created_at: "2026-10-10T00:00:00Z", published_at: status === "DRAFT" ? null : "2026-10-11T00:00:00Z",
  publisher: null, question_count: 20, total_marks: "80.00", sections: [{ position: 1, subject_id: "phy", subject: "Physics", paper_id: "gp", paper_title: "Platform Physics",
    paper_version_id: "gpv", version_no: 2, version_status: "PUBLISHED", question_count: 20, total_marks: "80.00" }], kind: "SECTIONS", subjects: [], marking_mode: "PER_QUESTION", marks_correct: null, marks_wrong: null, generate_lead_minutes: 60, occurrences: {}, next_occurrence_at: null, ...extra });
afterEach(() => vi.unstubAllGlobals());

describe("Exam Plans", () => {
  it("lists plans with their audience, marking and draft status, and activates a draft", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["examplan.manage", "exam.generate"]),
      "/api/v1/branches/b1/exam-plans": page([{ plan_id: "pl1", name: "Weekly Physics", duration_minutes: 90, start_at: "2026-10-20T04:00:00Z", end_at: "2026-10-20T08:00:00Z",
        generate_at: null, auto_publish: true, status: "DRAFT", audience_type: "INSTITUTE", batch_ids: [], student_ids: [], marking_mode: "UNIFORM", marks_correct: 4,
        marks_wrong: 1, global_test_id: null, global_test_name: null, slots: [{ subject_id: "phy", paper_id: null, paper_version_id: "v" }] }]),
      "PATCH /api/v1/exam-plans/pl1": { status: "ACTIVE" } });
    renderApp("/exam-plans");
    expect(await screen.findByText("Weekly Physics")).toBeTruthy();
    expect(screen.getByText("Whole institute")).toBeTruthy();
    expect(screen.getByText("+4 / −1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Create a test" }).getAttribute("href")).toBe("/exam-plans/new");
    expect(screen.queryByRole("button", { name: "Generate now" })).toBeNull();          // drafts never generate
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "ACTIVE" }));
  });

  it("lets providers schedule published Global Tests but not manage them", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["examplan.manage"]), "/api/v1/global-tests": page([test("PUBLISHED")]) });
    renderApp("/tests/global?tab=tests");
    expect(await screen.findByText("Daily Physics #7")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Schedule" }).getAttribute("href")).toBe("/tests/global/gt1/schedule");
    expect(screen.queryByRole("button", { name: /New Global Test|Publish|Withdraw/ })).toBeNull();
  });

  it("lets the platform publish and withdraw a test, with a reason and an explanation of the impact", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage"]), "/api/v1/global-tests": page([test("PUBLISHED")]),
      "POST /api/v1/global-tests/gt1/withdraw": test("WITHDRAWN") });
    renderApp("/exam-plans?tab=global");
    fireEvent.click(await screen.findByRole("button", { name: "Withdraw" }));
    const dialog = await screen.findByRole("dialog", { name: "Withdraw “Daily Physics #7”" });
    expect(within(dialog).getByText(/Exams already generated, attempts and results are unchanged/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Reason/), { target: { value: "Wrong key in Q3" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Withdraw" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/withdraw"))?.body).toEqual({ reason: "Wrong key in Q3" }));
    expect(screen.getByRole("button", { name: "Create new version" })).toBeTruthy();
  });
});

describe("Students & Enrollments directory", () => {
  it("shows one row per student identity with all the enrollments in scope, and searches by ID", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["enrollment.manage"]),
      "/api/v1/students": page([{ student_id: "STU000001", name: "Riya Patil", account_status: "ACTIVE", enrollments: [
        { enrollment_id: "e1", provider_id: "p1", provider_name: "Alpha", branch_id: "b1", branch_name: "Main", status: "ACTIVE", enrolled_at: "2026-09-01T00:00:00Z", batches: ["NEET A"] },
        { enrollment_id: "e2", provider_id: "p2", provider_name: "Beta", branch_id: "b9", branch_name: "City", status: "ACTIVE", enrolled_at: "2026-09-05T00:00:00Z", batches: [] }] }]) });
    renderApp("/students");
    expect(await screen.findByText("Riya Patil")).toBeTruthy();
    expect(screen.getByText("Alpha / Main; Beta / City")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "STU0000" } });
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/v1/students").at(-1)!.search.get("q")).toBe("STU0000"));
  });
});

describe("Audit Log", () => {
  it("sends date, actor, provider and record-type filters to the API", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["audit.read"]),
      "/api/v1/audit-logs": page([{ audit_id: "a1", at: "2026-10-17T09:00:00Z", actor: "s", actor_code: "STF000002", actor_name: "Asha Rao", action: "staff.deactivate",
        target_type: "staff", target_id: "abcd1234-0000", provider_id: null, provider_name: null, outcome: "SUCCESS", reason: "left" }]) });
    renderApp("/audit");
    expect(await screen.findByText("Asha Rao")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("Actor (Staff ID)"), { target: { value: "STF000002" } });
    fireEvent.change(screen.getByLabelText("Record type"), { target: { value: "staff" } });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "p1" } });
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/api/v1/audit-logs").at(-1)!.search;
      expect([last.get("actor_code"), last.get("target_type"), last.get("provider_id"), !!last.get("since")]).toEqual(["STF000002", "staff", "p1", true]);
    });
  });
});

describe("Unified Global Test exam, provider view", () => {
  it("explains the subject-only view, offers no control actions and labels the marks with the provider's subjects", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["exam.read", "exam.schedule", "exam.publish", "result.read"]),
      "/api/v1/exams/ex1": { exam_id: "ex1", plan_id: "pl9", provider_id: "p1", branch_id: "b1", name: "NEET UG - 02 (2026-11-07)", status: "PUBLISHED",
        start_at: "2026-11-07T04:30:00Z", end_at: "2026-11-07T10:30:00Z", duration_minutes: 180, total_questions: 90, total_marks: "360.00", missing_subjects: [],
        papers: [{ subject_id: "phy", source_provider_id: "p1", paper_version_id: "v-phy-1", question_count: 45 }, { subject_id: "chem", source_provider_id: "p1", paper_version_id: "v-chem-1", question_count: 45 }],
        assigned_students: 2, assignment_status: { ASSIGNED: 2 }, delivery_status: "SENT", settings: { marking: { mode: "UNIFORM", correct: 4, wrong: 1 }, global_test_id: "gt9" },
        unified_global: true, visible_subject_ids: ["chem", "phy"] },
      "/api/v1/exams/ex1/results": { window_closed: true, total_marks: "360.00", subject_view: true, visible_subjects: ["Chemistry", "Physics"],
        summary: { assigned: 2, submitted: 1, not_submitted: 1, average: 120, highest: 120, lowest: 120 },
        results: [{ rank: 1, student_id: "STU000001", name: "Riya Patil", obtained_marks: 120, total_marks: 360, accuracy: 80 }] } });
    renderApp("/exams/ex1");
    expect(await screen.findByText(/You see only the subjects your/)).toBeTruthy();
    expect(screen.getByText(/each subject is the paper its institute submitted/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel exam" })).toBeNull();               // the platform owns the exam
    fireEvent.click(screen.getByRole("button", { name: "Load results" }));
    expect(await screen.findByText("Marks (Chemistry, Physics)")).toBeTruthy();
    expect(screen.getByText("120/360")).toBeTruthy();
  });
});
