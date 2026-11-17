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

  it("asks a test's dates together with the test, and a failure to load them stays in the dates section", async () => {
    let release: (v: unknown) => void = () => {};
    const pending = new Promise((r) => { release = r; });
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/global-tests/gt1": () => pending,
      "/api/v1/tests": page([]), "/api/v1/global-tests/gt1/occurrences": fail(500, { code: "ERROR", message: "Dates unavailable." }) });
    renderApp("/tests/global/gt1");
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/global-tests/gt1/occurrences")).toBe(true));   // before the test answered
    release(test_({ status: "PUBLISHED" }));
    expect(await screen.findByText("Dates unavailable.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "2. Schedule" })).toBeTruthy();                                // the page itself is intact
    expect(calls.filter((c) => c.path === "/api/v1/global-tests/gt1/occurrences")).toHaveLength(1);           // the section reuses the request
  });

  it("never shows the dates' 404 on a test that has no dates (an older platform-papers test)", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/tests": page([]),
      "/api/v1/global-tests/gt1": test_({ kind: "SECTIONS", status: "PUBLISHED", sections: [] }), "/api/v1/global-test-contributions": page([]),
      "/api/v1/global-tests/gt1/occurrences": fail(404, { code: "NOT_FOUND", message: "Global test not found." }) });
    renderApp("/tests/global/gt1");
    expect(await screen.findByRole("heading", { level: 2, name: "National Mock" })).toBeTruthy();
    expect(screen.queryByText("Global test not found.")).toBeNull();
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

  // the teacher's submit set-up: two of her own papers, her batch NEET A in Pune (a batch-level teacher)
  const submitSetup = (post: (u: URL, init: RequestInit) => unknown) => stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["paper.read", "paper.upload", "exam.read"]),
    "/api/v1/global-occurrences/o1": { ...occ(), submission_list: [], coverage: [] },
    "/api/v1/global-occurrences/o1/scope": { provider_id: "p1", provider_name: "Alpha", submissions_open: true, branches: [
      { branch_id: "b1", name: "Pune", whole_branch_allowed: false, batches: [{ batch_id: "ba", name: "NEET A" }], subjects: [{ subject_id: "phy", name: "Physics" }] }] },
    "/api/v1/global-occurrences/o1/students": { items: [], total: 0 },
    "/api/v1/global-occurrences/o1/papers": { items: [
      { paper_id: "pp", title: "Physics A", paper_version_id: "pvA", version_no: 1, version_status: "PUBLISHED", ready: true, question_count: 3 },
      { paper_id: "pq", title: "Physics B", paper_version_id: "pvB", version_no: 1, version_status: "PUBLISHED", ready: true, question_count: 3 }], total: 2 },
    "POST /api/v1/global-occurrences/o1/submissions": post });
  const fillSubmit = async (paper: string) => {
    fireEvent.click(await screen.findByRole("button", { name: "Submit a paper" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(await within(dialog).findByLabelText("Your paper"), { target: { value: paper } });
    fireEvent.click(within(dialog).getByLabelText("Pune"));
    expect(within(dialog).queryByLabelText("Every student of Pune")).toBeNull();                              // batch-level teacher
    fireEvent.click(await within(dialog).findByLabelText("NEET A"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Submit paper" }));
    return dialog;
  };
  const audience = { provider_id: "p1", branch_ids: ["b1"], whole_branch_ids: [], batch_ids: ["ba"], student_ids: [] };

  it("submits a different paper for students who already receive one (they will choose), with an idempotency key", async () => {
    // since per-student paper choice the API accepts a DIFFERENT paper for an overlapping audience: no refusal is involved
    const calls = submitSetup(() => SUB);
    renderApp("/tests/global/occurrences/o1");
    expect(await screen.findByText(/a student reached by two papers of a subject chooses one/)).toBeTruthy();   // the rule, as the page states it
    await fillSubmit("pvA");
    expect(await screen.findByText("Submitted “Physics A” for 2 student(s).")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const sent = posts(calls, "/api/v1/global-occurrences/o1/submissions");
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({ subject_id: "phy", paper_version_id: "pvA", audience });
    expect(sent[0].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("shows the refusal of the same paper submitted twice for the same students; a retry reuses its key, another paper gets a new one", async () => {
    const DUPLICATE = { code: "DUPLICATE_PAPER_SUBMISSION", message: "This paper version is already submitted for 2 of these students for this subject "
      + "on this date. Choose other students or another paper.", overlapping_students: 2 };
    const calls = submitSetup((_u, init) => (JSON.parse(String(init.body)).paper_version_id === "pvA" ? fail(409, DUPLICATE)    // pvA already submitted
      : { ...SUB, submission_id: "s2", paper_title: "Physics B", paper_version_id: "pvB" }));
    renderApp("/tests/global/occurrences/o1");
    const dialog = await fillSubmit("pvA");
    expect(await within(dialog).findByText(/already submitted for 2 of these students/)).toBeTruthy();       // the API's own message
    expect(screen.queryByText(/^Submitted “/)).toBeNull();                                                   // nothing was submitted
    fireEvent.click(within(dialog).getByRole("button", { name: "Submit paper" }));                           // the same request again
    await waitFor(() => expect(posts(calls, "/api/v1/global-occurrences/o1/submissions")).toHaveLength(2));
    expect(await within(dialog).findByText(/already submitted for 2 of these students/)).toBeTruthy();       // still refused
    fireEvent.change(within(dialog).getByLabelText("Your paper"), { target: { value: "pvB" } });              // another paper for them
    fireEvent.click(within(dialog).getByRole("button", { name: "Submit paper" }));
    expect(await screen.findByText("Submitted “Physics B” for 2 student(s).")).toBeTruthy();
    const sent = posts(calls, "/api/v1/global-occurrences/o1/submissions");
    expect(sent.map((c) => (c.body as { paper_version_id: string }).paper_version_id)).toEqual(["pvA", "pvA", "pvB"]);
    expect(sent[2].body).toEqual({ subject_id: "phy", paper_version_id: "pvB", audience });
    expect(sent[1].headers["Idempotency-Key"]).toBe(sent[0].headers["Idempotency-Key"]);                     // the same request retried
    expect(sent[2].headers["Idempotency-Key"]).not.toBe(sent[0].headers["Idempotency-Key"]);                 // a different request
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

  const notGenerated = { ...occ(), submission_list: [], coverage: [] };
  it("asks the layout together with the detail unless the opening link said the date is not generated", async () => {
    let release: (v: unknown) => void = () => {};
    const pending = new Promise((r) => { release = r; });
    let calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/global-occurrences/o1": () => pending,
      "/api/v1/global-occurrences/o1/student-subjects": LAYOUT });
    let view = renderApp("/tests/global/occurrences/o1", { generated: true });
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/student-subjects"))).toBe(true));   // before the detail answered
    release(generated);
    expect(await screen.findByRole("heading", { name: "Students" })).toBeTruthy();
    view.unmount();
    vi.unstubAllGlobals();
    // the list said "not generated": nothing is asked, and nothing is shown, for a date the detail confirms is not generated
    calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/global-occurrences/o1": notGenerated,
      "/api/v1/global-occurrences/o1/student-subjects": LAYOUT });
    view = renderApp("/tests/global/occurrences/o1", { generated: false });
    await screen.findByRole("heading", { name: /National Mock:/ });
    expect(calls.some((c) => c.path.endsWith("/student-subjects"))).toBe(false);
    view.unmount();
    vi.unstubAllGlobals();
    // a direct link (no hint) to a date that is not generated: the layout may be asked, but is never shown
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]), "/api/v1/global-occurrences/o1": notGenerated,
      "/api/v1/global-occurrences/o1/student-subjects": { ...LAYOUT, generated: false } });
    renderApp("/tests/global/occurrences/o1");
    await screen.findByRole("heading", { name: /National Mock:/ });
    expect(screen.queryByRole("heading", { name: "Students" })).toBeNull();
  });

  it("shows only the detail's 404 for a missing date, whatever the layout request answers", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "exam.read"]),
      "/api/v1/global-occurrences/o1": fail(404, { code: "NOT_FOUND", message: "Scheduled Global Test not found." }),
      "/api/v1/global-occurrences/o1/student-subjects": fail(403, { code: "FORBIDDEN", message: "You do not have permission for this action." }) });
    renderApp("/tests/global/occurrences/o1", { generated: true });
    expect(await screen.findByText("Scheduled Global Test not found.")).toBeTruthy();
    expect(screen.queryByText(/do not have permission/)).toBeNull();
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
