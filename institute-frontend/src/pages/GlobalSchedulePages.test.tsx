import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi, type Call } from "../test/stubApi";

const SUBJECTS = page([{ subject_id: "phy", name: "Physics", status: "ACTIVE" }, { subject_id: "chem", name: "Chemistry", status: "ACTIVE" }]);
const test_ = (over: Record<string, unknown> = {}) => ({ test_id: "gt1", name: "National Mock", description: null, exam_type: "NEET", target_class: null, duration_minutes: 60,
  status: "DRAFT", derived_from_test_id: null, created_at: "", published_at: null, publisher: null, question_count: 0, total_marks: "0", sections: [], kind: "SCHEDULE",
  subjects: [{ subject_id: "phy", subject: "Physics" }, { subject_id: "chem", subject: "Chemistry" }], marking_mode: "UNIFORM", marks_correct: 4, marks_wrong: 1,
  generate_lead_minutes: 120, occurrences: {}, next_occurrence_at: null, ...over });
const occ = (over: Record<string, unknown> = {}) => ({ occurrence_id: "o1", test_id: "gt1", test_name: "National Mock", test_status: "PUBLISHED", exam_type: "NEET",
  target_class: null, duration_minutes: 60, marking: { mode: "UNIFORM", correct: 4, wrong: 1 }, start_at: "2030-01-05T04:30:00Z", end_at: "2030-01-05T07:30:00Z",
  status: "PUBLISHED", submission_deadline: "2030-01-05T02:30:00Z", submissions_open: true, generated_at: null, generation_error: null, cancel_reason: null,
  subjects: [{ subject_id: "phy", subject: "Physics", can_submit: true }, { subject_id: "chem", subject: "Chemistry", can_submit: false }],
  submissions: { active: 0, withdrawn: 0 }, ...over });
const SUB = { submission_id: "s1", subject_id: "phy", subject: "Physics", provider_id: "p1", provider_name: "Alpha", branch_name: "Pune", status: "ACTIVE",
  paper_title: "Physics A", paper_version_id: "pvA", version_no: 1, version_status: "PUBLISHED", ready: true, question_count: 3,
  audience: { entire_branches: [], batches: ["NEET A"], students: 0 }, students_now: 2, submitted_by_name: "Tara", submitted_at: "", withdraw_reason: null, can_withdraw: true };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/subjects": SUBJECTS,
  "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]), "/api/v1/providers/p1/branches": page([{ branch_id: "b1", name: "Pune" }]) };
const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
afterEach(() => vi.unstubAllGlobals());

describe("Global Tests for the platform", () => {
  it("creates a Global Test from subjects, duration and marking, without any platform paper", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/global-tests": page([]),
      "POST /api/v1/global-tests": test_() });
    renderApp("/tests/global");
    fireEvent.click(await screen.findByRole("button", { name: "New Global Test" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/approved platform papers/i)).toBeNull();
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "National Mock" } });
    fireEvent.click(await within(dialog).findByLabelText("Physics"));
    fireEvent.click(within(dialog).getByLabelText("Chemistry"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText(/Open it to plan and publish its dates/)).toBeTruthy();
    expect(posts(calls, "/api/v1/global-tests")[0].body).toMatchObject({ name: "National Mock", subject_ids: ["phy", "chem"], marking_mode: "UNIFORM",
      marks_correct: 4, marks_wrong: 1, generate_lead_minutes: 120 });
    expect(posts(calls, "/api/v1/global-tests")[0].body).not.toHaveProperty("sections");
  });

  it("plans several months of dates, then publishes the test and its dates once", async () => {
    let published = false;
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]),
      "/api/v1/global-tests/gt1": () => test_(published ? { status: "PUBLISHED" } : {}),
      "/api/v1/global-tests/gt1/occurrences": () => ({ items: [occ({ status: published ? "PUBLISHED" : "DRAFT", start_at: "2099-01-05T04:30:00Z" })] }),
      "POST /api/v1/global-tests/gt1/occurrences/plan": { created: 13, existing: 0, skipped_past: 0, items: [] },
      "POST /api/v1/global-tests/gt1/publish": () => { published = true; return test_({ status: "PUBLISHED" }); } });
    renderApp("/tests/global/gt1");
    expect(await screen.findByRole("heading", { name: "2. Schedule" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Plan dates for months" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("First month"), { target: { value: "2099-01" } });
    fireEvent.change(within(dialog).getByLabelText("Number of months"), { target: { value: "3" } });
    fireEvent.change(within(dialog).getByLabelText("Time zone"), { target: { value: "Asia/Kolkata" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Plan dates" }));
    expect(await screen.findByText("Planned 13 new draft date(s).")).toBeTruthy();
    expect(posts(calls, "/api/v1/global-tests/gt1/occurrences/plan")[0].body).toMatchObject({ months: ["2099-01", "2099-02", "2099-03"], weekdays: [5],
      start_time: "10:00", timezone: "Asia/Kolkata", window_minutes: 180 });
    expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();                                       // drafts can be changed
    fireEvent.click(screen.getByRole("button", { name: "Publish test and dates" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Publish" }));
    expect(await screen.findByText(/Providers can now submit their papers/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Edit" })).toBeNull());                // published: locked
  });
});

describe("Global Tests for provider staff", () => {
  it("lists the published dates with a Submit paper entry point", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["paper.read", "paper.upload", "paper.publish", "exam.read"]), "/api/v1/global-schedule": page([occ()]) });
    renderApp("/tests/global");
    expect(await screen.findByText("National Mock")).toBeTruthy();
    expect(screen.getByText("· you can submit")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Submit paper" }).getAttribute("href")).toBe("/tests/global/occurrences/o1");
    expect(screen.queryByRole("button", { name: "New Global Test" })).toBeNull();
  });

  it("submits the teacher's own paper for an authorized audience, with an idempotency key, and shows a refused overlap", async () => {
    let attempt = 0;
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["paper.read", "paper.upload", "exam.read"]),
      "/api/v1/global-occurrences/o1": { ...occ(), submission_list: [], coverage: [] },
      "/api/v1/global-occurrences/o1/scope": { provider_id: "p1", provider_name: "Alpha", submissions_open: true, branches: [
        { branch_id: "b1", name: "Pune", whole_branch_allowed: false, batches: [{ batch_id: "ba", name: "NEET A" }], subjects: [{ subject_id: "phy", name: "Physics" }] }] },
      "/api/v1/global-occurrences/o1/students": { items: [], total: 0 },
      "/api/v1/global-occurrences/o1/papers": { items: [{ paper_id: "pp", title: "Physics A", paper_version_id: "pvA", version_no: 1, version_status: "PUBLISHED", ready: true, question_count: 3 }], total: 1 },
      "POST /api/v1/global-occurrences/o1/submissions": () => (++attempt === 1
        ? fail(409, { code: "AUDIENCE_OVERLAP", message: "1 of these students already receive another paper for this subject on this date." }) : SUB) });
    renderApp("/tests/global/occurrences/o1");
    fireEvent.click(await screen.findByRole("button", { name: "Submit a paper" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(await within(dialog).findByLabelText("Your paper"), { target: { value: "pvA" } });
    fireEvent.click(within(dialog).getByLabelText("Pune"));
    expect(within(dialog).queryByLabelText("Every student of Pune")).toBeNull();                              // batch-level teacher
    fireEvent.click(await within(dialog).findByLabelText("NEET A"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Submit paper" }));
    expect(await within(dialog).findByText(/already receive another paper/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Submit paper" }));
    expect(await screen.findByText("Submitted “Physics A” for 2 student(s).")).toBeTruthy();
    const sent = posts(calls, "/api/v1/global-occurrences/o1/submissions");
    expect(sent[0].body).toEqual({ subject_id: "phy", paper_version_id: "pvA", audience: { provider_id: "p1", branch_ids: ["b1"], whole_branch_ids: [], batch_ids: ["ba"], student_ids: [] } });
    expect(sent[0].headers["Idempotency-Key"]).toBeTruthy();
    expect(sent[1].headers["Idempotency-Key"]).toBe(sent[0].headers["Idempotency-Key"]);                     // the same request retried
  });

  it("shows submissions and coverage, and closes papers after the deadline", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["paper.read", "paper.upload", "exam.read", "examplan.manage"]),
      "/api/v1/global-occurrences/o1": { ...occ({ submissions_open: false, generated_at: "2030-01-05T02:31:00Z" }), submission_list: [{ ...SUB, can_withdraw: true }],
        coverage: [{ provider_id: "p1", provider_name: "Alpha", subject_id: "chem", subject: "Chemistry", submissions: 0, ready: 0, students_covered: 0, choosing: 0, missing: true }],
        exams: [{ exam_id: "e1", name: "National Mock (2030-01-05)", status: "PUBLISHED", assigned: 2 }] } });
    renderApp("/tests/global/occurrences/o1");
    expect(await screen.findByText("Physics A v1")).toBeTruthy();
    expect(screen.getByText("Missing paper")).toBeTruthy();
    expect(screen.getByText(/each student sits their own papers/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Submit a paper" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
    expect(screen.getByRole("link", { name: "National Mock (2030-01-05)" }).getAttribute("href")).toBe("/exams/e1");
  });

  it("warns about the question limit and lets only the Super Admin retry a stopped date", async () => {
    const stoppedDate = { ...occ({ submissions_open: false, generation_failed_at: "2030-01-05T02:40:00Z", generation_error: "QUESTION_LIMIT_EXCEEDED: 5200" }),
      submission_list: [], coverage: [], question_limit: { limit: 5000, exceeded: true, projected: 5200, mine: null } };
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/global-occurrences/o1": stoppedDate,
      "POST /api/v1/global-occurrences/o1/generation/retry": { retried: true } });
    renderApp("/tests/global/occurrences/o1");
    expect(await screen.findByText(/more questions than one exam may \(5200 of 5000/)).toBeTruthy();
    expect(screen.getByText("Generation stopped")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry generation" }));
    await waitFor(() => expect(posts(calls, "/api/v1/global-occurrences/o1/generation/retry")).toHaveLength(1));
    expect(posts(calls, "/api/v1/global-occurrences/o1/generation/retry")[0].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("shows no retry to a provider", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["paper.read", "paper.upload", "exam.read"]),
      "/api/v1/global-occurrences/o1": { ...occ({ submissions_open: false, generation_failed_at: "2030-01-05T02:40:00Z" }), submission_list: [], coverage: [] } });
    renderApp("/tests/global/occurrences/o1");
    expect(await screen.findByText(/Generating this date stopped/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry generation" })).toBeNull();
  });

  const generated = { ...occ({ submissions_open: false, generated_at: "2030-01-05T02:31:00Z" }), submission_list: [], coverage: [], exams: [] };
  const LAYOUT = { scope: "ALL", subjects: [{ subject_id: "phy", subject: "Physics", position: 1 }, { subject_id: "chem", subject: "Chemistry", position: 2 }],
    students: [{ student_id: "STU000010", name: "Asha K", exam_id: "e1", exam_status: "PUBLISHED", subjects: [
      { subject_id: "phy", subject: "Physics", status: "PAPER", message: null, provider_name: "Alpha" },
      { subject_id: "chem", subject: "Chemistry", status: "NOT_ENROLLED", message: "You are not enrolled in Chemistry with any of your institutes.", provider_name: null }] }] };

  it("shows each student's subjects in the configured order with their paper or the reason", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/global-occurrences/o1": generated,
      "/api/v1/global-occurrences/o1/student-subjects": LAYOUT });
    renderApp("/tests/global/occurrences/o1");
    const card = (await screen.findByRole("heading", { name: "Students" })).closest(".card") as HTMLElement;
    const headers = within(card).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Student", "Physics", "Chemistry", "Exam"]);
    expect(within(card).getByText("Alpha")).toBeTruthy();
    expect(within(card).getByText("Not enrolled").closest("[title]")?.getAttribute("title")).toMatch(/not enrolled in Chemistry/);
  });

  it("never asks the layout for a teacher", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["paper.read", "paper.upload", "exam.read"]), "/api/v1/global-occurrences/o1": generated,
      "/api/v1/global-occurrences/o1/student-subjects": LAYOUT });
    renderApp("/tests/global/occurrences/o1");
    await screen.findByText(/each student sits their own papers/);
    expect(calls.some((c) => c.path.endsWith("/student-subjects"))).toBe(false);
    expect(screen.queryByRole("heading", { name: "Students" })).toBeNull();
  });
});
