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

const scheduled = (over: Record<string, unknown>) => ({ plan_id: "pl1", test_type: "DAILY", series_id: null, global_test_id: null, exam_id: null, exam_version_id: null,
  name: "Weekly Physics", state: "NOT_PUBLISHED", plan_status: "DRAFT", exam_status: null, delivery_status: null, error: null, provider_name: "Alpha", home_branch: "Main",
  start_at: "2026-10-20T04:00:00Z", end_at: "2026-10-20T08:00:00Z", duration_minutes: 90, subjects: ["Physics"], audience: { type: "INSTITUTE", branches: [], batches: [], students: 0 },
  assigned_students: 0, question_count: null, total_marks: null, marking: null, created_at: "2026-10-10T00:00:00Z", ...over });

describe("Exam plans, folded into Scheduled Tests", () => {
  it("sends the old Exam Plans page to Scheduled Tests, where a draft is activated and an active plan generated", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["examplan.manage", "exam.generate", "test.create"]),
      "/api/v1/tests": page([scheduled({}), scheduled({ plan_id: "pl2", name: "Daily Chemistry", plan_status: "ACTIVE" }),
                             scheduled({ plan_id: "pl3", name: "Done", plan_status: "ACTIVE", exam_id: "ex3", state: "PUBLISHED" })]),
      "PATCH /api/v1/exam-plans/pl1": { status: "ACTIVE" },
      "POST /api/v1/exam-plans/pl2/generate": { job_id: "j", status: "SUCCEEDED", exam_id: "ex2", error: null } });
    renderApp("/exam-plans");
    expect(await screen.findByText("Weekly Physics")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Scheduled Tests" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Activate" })).toHaveLength(1);       // drafts only
    expect(screen.getAllByRole("button", { name: "Generate now" })).toHaveLength(1);   // active plans without an exam only
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "ACTIVE" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate now" }));
    expect(await screen.findByRole("link", { name: "Open the exam" })).toBeTruthy();
  });

  it("offers neither action without the permissions", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["exam.read"]),
      "/api/v1/tests": page([scheduled({}), scheduled({ plan_id: "pl2", plan_status: "ACTIVE" })]) });
    renderApp("/tests");
    expect(await screen.findAllByText("Weekly Physics")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Activate|Generate now/ })).toBeNull();
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
  const sub = (subject: string, batch: string, chosen = false) => ({ subject_id: subject.toLowerCase(), subject, batch, chosen });
  const enr = (id: string, provider: string, branch: string, status: string, subjects: ReturnType<typeof sub>[]) => ({ enrollment_id: id, provider_id: provider,
    provider_name: provider, branch_id: branch, branch_name: branch, status, enrolled_at: "2026-09-01T00:00:00Z", batches: subjects.map((x) => x.batch), subjects });

  it("shows which provider teaches the student each subject, and searches by ID", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["enrollment.manage"]),
      "/api/v1/students": page([{ student_id: "STU000001", name: "Riya Patil", account_status: "ACTIVE", enrollments: [
        enr("e1", "Apex NEET Academy", "Mumbai", "ACTIVE", [sub("Physics", "NEET A", true)]),
        enr("e2", "Bright Biology Classes", "Pune", "ACTIVE", [sub("Biology", "NEET B"), sub("Chemistry", "NEET B")]),
        enr("e3", "Core Science", "Navi Mumbai", "INACTIVE", [sub("Biology", "NEET C")]),
        enr("e4", "Delta Tutors", "Home", "ACTIVE", [])] }]) });
    renderApp("/students");
    expect(await screen.findByText("Riya Patil")).toBeTruthy();
    expect(screen.getByText("Biology — Bright Biology Classes")).toBeTruthy();          // subject — provider, sorted by subject
    expect(screen.getByText(/Biology — Core Science \(inactive\)/)).toBeTruthy();
    expect(screen.getByText("Chemistry — Bright Biology Classes")).toBeTruthy();
    expect(screen.getByText("Delta Tutors / Home: no subject assigned (not in a batch)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "+1 more" }));                    // Physics — Apex, in the details
    const dialog = await screen.findByRole("dialog", { name: "Subjects of Riya Patil" });
    expect(within(dialog).getByText("Apex NEET Academy")).toBeTruthy();
    expect(within(dialog).getByText("Chosen in the batch")).toBeTruthy();
    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "STU0000" } });
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/v1/students").at(-1)!.search.get("q")).toBe("STU0000"));
  });

  it("flags a subject taught by more than one provider instead of choosing one", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["enrollment.manage"]),
      "/api/v1/students": page([{ student_id: "STU000002", name: "Arun", account_status: "ACTIVE", enrollments: [
        enr("e1", "Apex", "Mumbai", "ACTIVE", [sub("Physics", "A")]), enr("e2", "Core", "Pune", "ACTIVE", [sub("Physics", "C")])] }]) });
    renderApp("/students");
    expect(await screen.findByText("Arun")).toBeTruthy();
    expect(screen.getAllByText(/taught by 2 providers/)).toHaveLength(2);
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
    fireEvent.click(screen.getAllByRole("link", { name: "Results" }).find((a) => a.getAttribute("href") === "/results/ex1")!);   // results: own page
    expect(await screen.findByText("Marks (Chemistry, Physics)")).toBeTruthy();
    expect(screen.getByText("120/360")).toBeTruthy();
    expect(screen.getByText(/the overall score is visible only to the student and the platform/)).toBeTruthy();
  });
});
