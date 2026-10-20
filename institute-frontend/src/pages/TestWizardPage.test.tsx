import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi, type Call } from "../test/stubApi";

const PROVIDER = { provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" };
const BRANCH = { branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" };
const PHY = { subject_id: "phy", code: "PHYSICS", name: "Physics", status: "ACTIVE" };
const PA = me("PROVIDER_ADMIN", ["examplan.manage", "exam.generate", "question.read", "paper.read", "paper.create", "paper.approve", "paper.publish", "enrollment.manage"]);
const question = (id: string) => ({
  question_id: id, scope: "PROVIDER", provider_id: "p1", branch_id: "b1", subject_id: "phy", status: "ACTIVE", exam_type: null, target_class: null, chapter: null,
  topic: null, subtopic: null, difficulty: null, pattern: "SINGLE_CORRECT", subtype: null, question_type: "MCQ_SINGLE", created_at: "2026-10-01T00:00:00Z",
  usage_count: 0, can_edit: true, latest: { question_version_id: `${id}v`, question_id: id, version_no: 1, stem: `Question ${id}?`, option_a: "a", option_b: "b",
    option_c: "c", option_d: "d", correct_option: "A", explanation: null, topic: null, marks_positive: "4", marks_negative: "1" },
});
const GT = { test_id: "gt1", name: "Daily Physics #7", description: null, exam_type: "NEET", target_class: "Class 12", duration_minutes: 30, status: "PUBLISHED",
  derived_from_test_id: null, created_at: "2026-10-10T00:00:00Z", published_at: "2026-10-11T00:00:00Z", publisher: "Platform Owner", question_count: 20, total_marks: "80.00",
  sections: [{ position: 1, subject_id: "phy", subject: "Physics", paper_id: "gp", paper_title: "Platform Physics", paper_version_id: "gpv", version_no: 2,
    version_status: "PUBLISHED", question_count: 20, total_marks: "80.00" }] };

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/api/v1/auth/me": PA, "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([PROVIDER]), "/api/v1/providers/p1/branches": page([BRANCH]),
    "/api/v1/providers/p1/subjects": [PHY], "/api/v1/subjects": page([PHY]), "/api/v1/branches/b1/batches": page([{ batch_id: "ba", name: "NEET 2027 A", status: "ACTIVE" }]),
    "/api/v1/question-taxonomy": { exam_type: [], target_class: [], chapter: [], topic: [], subtopic: [], difficulty: [], pattern: [] },
    "/api/v1/questions": page([question("q1"), question("q2")]),
    "POST /api/v1/branches/b1/audience-preview": (_u: URL, init: RequestInit) => ({ eligible_count: JSON.parse(init.body as string).audience_type === "BRANCH" ? 42 : 17, sample_student_ids: [], not_eligible_student_ids: [] }),
    "POST /api/v1/branches/b1/exam-plans/check": { ok: true }, "POST /api/v1/papers/check": { ok: true, question_count: 2, total_marks: "8.00" },
    "POST /api/v1/papers": { paper_id: "np", paper_version_id: "npv", version_no: 1, status: "VALIDATED", question_count: 2, total_marks: "8.00" },
    "POST /api/v1/paper-versions/npv/approve": { status: "APPROVED" }, "POST /api/v1/paper-versions/npv/publish": { status: "PUBLISHED" },
    "POST /api/v1/branches/b1/exam-plans": (_u: URL, init: RequestInit) => ({ plan_id: "plan1", name: JSON.parse(init.body as string).name, status: JSON.parse(init.body as string).status }),
    "POST /api/v1/exam-plans/plan1/generate": { job_id: "j1", status: "SUCCEEDED", exam_id: "ex1", error: null },
    "/api/v1/global-tests/gt1": GT, "/api/v1/global-tests": page([GT]),
    "POST /api/v1/global-tests/gt1/schedule": (_u: URL, init: RequestInit) => ({ plan_id: "plan2", name: JSON.parse(init.body as string).name, status: "ACTIVE" }),
    ...extra,
  };
}
const next = async () => fireEvent.click(await screen.findByRole("button", { name: "Next" }));
const body = (calls: Call[], method: string, path: string) => calls.find((c) => c.method === method && c.path === path)?.body as Record<string, unknown>;
afterEach(() => vi.unstubAllGlobals());

describe("test creation wizard", () => {
  it("follows Choose test -> Audience -> Source -> Configure -> Review and schedules a new test from bank questions", async () => {
    const calls = stubApi(routes());
    renderApp("/exam-plans/new");
    expect(await screen.findByRole("heading", { level: 1, name: "Exam Plans" })).toBeTruthy();
    const steps = within(await screen.findByRole("list", { name: "Steps" })).getAllByRole("listitem").map((li) => li.textContent?.replace(/^\d\s*/, ""));
    expect(steps).toEqual(["Choose test", "Audience", "Question source", "Configure", "Review & schedule"]);
    await next();                                                                       // Create a new test (default)

    expect(await screen.findByText("Who takes this test?")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);   // no batch chosen yet
    fireEvent.click(screen.getByLabelText("NEET 2027 A"));
    expect(await screen.findByText("17")).toBeTruthy();                           // eligible count from the server
    fireEvent.click(screen.getByLabelText(/The whole branch/));
    expect(await screen.findByText("42")).toBeTruthy();
    await next();

    fireEvent.click(await screen.findByLabelText("Select question: Question q1?"));
    fireEvent.click(screen.getByLabelText("Select question: Question q2?"));
    await next();

    fireEvent.change(await screen.findByLabelText("Test name"), { target: { value: "Weekly Physics" } });
    fireEvent.change(screen.getByLabelText("Marks"), { target: { value: "UNIFORM" } });
    fireEvent.change(screen.getByLabelText("Marks for a correct answer"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Marks deducted for a wrong answer"), { target: { value: "1" } });
    expect(screen.getByText(/Unanswered questions score 0/)).toBeTruthy();
    await next();

    expect(await screen.findByRole("heading", { name: "Review" })).toBeTruthy();
    expect(screen.getByText("+4 correct, −1 wrong, 0 unanswered")).toBeTruthy();
    expect(screen.getByText(/42 eligible student\(s\) now/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Schedule & generate now" }));
    expect(await screen.findByText("Test generated")).toBeTruthy();

    expect(body(calls, "POST", "/api/v1/papers")).toMatchObject({ scope: "PROVIDER", branch_id: "b1", subject_id: "phy", question_ids: ["q1", "q2"], title: "Weekly Physics – Physics" });
    expect(calls.some((c) => c.path === "/api/v1/paper-versions/npv/approve")).toBe(true);
    expect(calls.some((c) => c.path === "/api/v1/paper-versions/npv/publish")).toBe(true);
    expect(body(calls, "POST", "/api/v1/branches/b1/exam-plans")).toMatchObject({
      name: "Weekly Physics", audience_type: "BRANCH", batch_ids: [], student_ids: [], marking_mode: "UNIFORM", marks_correct: 4, marks_wrong: 1,
      slots: [{ subject_id: "phy", paper_version_id: "npv" }], status: "ACTIVE", duration_minutes: 180 });
    expect(calls.some((c) => c.path === "/api/v1/exam-plans/plan1/generate")).toBe(true);
  });

  it("schedules a Global Test from its own Global Tests page, without changing its papers, duration or marking", async () => {
    const calls = stubApi(routes());
    renderApp("/tests/global/gt1/schedule");
    expect(await screen.findByText("Who takes this test?")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Global Tests" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Schedule Global Test" })).toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Global Tests" }).getAttribute("href")).toBe("/tests/global");
    const steps = within(screen.getByRole("list", { name: "Steps" }));
    expect(steps.queryByText("Question source")).toBeNull();
    expect(steps.queryByText("Choose test")).toBeNull();
    fireEvent.click(screen.getByLabelText(/The whole institute/));                 // provider-level user
    await next();
    const duration = await screen.findByLabelText(/Duration/) as HTMLInputElement;
    expect([duration.value, duration.disabled]).toEqual(["30", true]);
    expect((screen.getByLabelText("Test name") as HTMLInputElement).value).toBe("Daily Physics #7");
    expect((screen.getByLabelText("Marks") as HTMLSelectElement).disabled).toBe(true);
    await next();
    expect(await screen.findByText(/Global Test “Daily Physics #7”/)).toBeTruthy();
    expect(screen.getByText("Total marks").nextElementSibling?.textContent).toBe("80");
    fireEvent.click(screen.getByRole("button", { name: "Schedule" }));
    expect(await screen.findByText("Test scheduled")).toBeTruthy();
    const sent = body(calls, "POST", "/api/v1/global-tests/gt1/schedule");
    expect(sent).toMatchObject({ branch_id: "b1", audience_type: "INSTITUTE", name: "Daily Physics #7", status: "ACTIVE" });
    expect(sent).not.toHaveProperty("duration_minutes");
    expect(sent).not.toHaveProperty("marking_mode");
    expect(sent).not.toHaveProperty("slots");
    expect(calls.some((c) => c.path === "/api/v1/papers" && c.method === "POST")).toBe(false);
    expect(screen.getByRole("link", { name: "Back to the Global Test" }).getAttribute("href")).toBe("/tests/global/gt1");
    expect(screen.queryByRole("button", { name: "Save as draft" })).toBeNull();
  });

  it("no longer offers Global Tests in the generic Exam Plans wizard, and sends old links to the Global Tests page", async () => {
    stubApi(routes());
    renderApp("/exam-plans/new");
    expect(await screen.findByText("What do you want to conduct?")).toBeTruthy();
    expect(screen.queryByText("Global Test", { exact: true })).toBeNull();
    expect(screen.getByRole("link", { name: "Test → Global Tests" }).getAttribute("href")).toBe("/tests/global");
  });

  it("redirects an old Exam Plans Global Test link to the dedicated schedule page", async () => {
    stubApi(routes());
    renderApp("/exam-plans/new?global=gt1");
    expect(await screen.findByRole("heading", { level: 2, name: "Schedule Global Test" })).toBeTruthy();
  });

  it("saves a draft that generates nothing, and never approves papers for a draft", async () => {
    const calls = stubApi(routes());
    renderApp("/exam-plans/new");
    await next();
    fireEvent.click(await screen.findByLabelText("NEET 2027 A"));
    await screen.findByText("17");
    await next();
    fireEvent.click(await screen.findByLabelText("Select question: Question q1?"));
    await next();
    fireEvent.change(await screen.findByLabelText("Test name"), { target: { value: "Draft test" } });
    await next();
    fireEvent.click(await screen.findByRole("button", { name: "Save as draft" }));
    expect(await screen.findByText("Draft saved")).toBeTruthy();
    expect(body(calls, "POST", "/api/v1/branches/b1/exam-plans")).toMatchObject({ status: "DRAFT", audience_type: "BATCHES", batch_ids: ["ba"], marking_mode: "PER_QUESTION" });
    expect(calls.some((c) => c.path.endsWith("/approve") || c.path.endsWith("/generate"))).toBe(false);
  });

  it("blocks Next until the step is valid and explains why", async () => {
    stubApi(routes());
    renderApp("/exam-plans/new");
    await next();
    fireEvent.click(await screen.findByLabelText(/Individual students/));
    expect(await screen.findByText("Choose at least one student.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(screen.queryByText(/eligible right now/)).toBeTruthy());
  });
});


describe("test creation wizard: nothing is left behind when the test cannot be created", () => {
  async function toReview() {
    renderApp("/exam-plans/new");
    await next();
    fireEvent.click(await screen.findByLabelText("NEET 2027 A"));
    expect(await screen.findByText("17")).toBeTruthy();
    await next();
    fireEvent.click(await screen.findByLabelText("Select question: Question q1?"));
    fireEvent.click(screen.getByLabelText("Select question: Question q2?"));
    await next();
    fireEvent.change(await screen.findByLabelText("Test name"), { target: { value: "Weekly Physics" } });
    await next();
    expect(await screen.findByRole("heading", { name: "Review" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Schedule & generate now" }));
  }
  const wrote = (calls: Call[]) => calls.filter((c) => c.method === "POST" && /^\/api\/v1\/(papers|paper-versions\/.*|branches\/b1\/exam-plans)$/.test(c.path)).map((c) => c.path);

  it("validates the plan and every paper before building, approving or publishing anything", async () => {
    const calls = stubApi(routes());
    await toReview();
    expect(await screen.findByText("Test generated")).toBeTruthy();
    const order = calls.filter((c) => c.method === "POST" && c.path !== "/api/v1/branches/b1/audience-preview").map((c) => c.path);
    expect(order).toEqual(["/api/v1/branches/b1/exam-plans/check", "/api/v1/papers/check", "/api/v1/papers", "/api/v1/paper-versions/npv/approve",
      "/api/v1/paper-versions/npv/publish", "/api/v1/branches/b1/exam-plans", "/api/v1/exam-plans/plan1/generate"]);
    expect(body(calls, "POST", "/api/v1/branches/b1/exam-plans/check")).toMatchObject({ name: "Weekly Physics", pending_subject_ids: ["phy"], batch_ids: ["ba"] });
  });

  it("creates no paper when the plan is invalid", async () => {
    const calls = stubApi(routes({ "POST /api/v1/branches/b1/exam-plans/check": fail(422, { code: "STUDENTS_NOT_ENROLLED", message: "Some students are not actively enrolled with this provider." }) }));
    await toReview();
    expect(await screen.findByText(/Some students are not actively enrolled/)).toBeTruthy();
    expect(wrote(calls)).toEqual([]);
  });

  it("creates no paper when a paper would not be valid", async () => {
    const calls = stubApi(routes({ "POST /api/v1/papers/check": fail(422, { code: "INVALID_QUESTIONS", message: "The paper was not created: fix the listed questions." }) }));
    await toReview();
    expect(await screen.findByText(/fix the listed questions/)).toBeTruthy();
    expect(wrote(calls)).toEqual([]);
    expect(calls.some((c) => c.path === "/api/v1/branches/b1/exam-plans/check")).toBe(true);
  });

  it("creates no plan when building the paper fails, and names papers that were already built if a later step fails", async () => {
    let calls = stubApi(routes({ "POST /api/v1/papers": fail(409, { code: "DUPLICATE_PAPER", message: "An identical paper already exists for this subject." }) }));
    await toReview();
    expect(await screen.findByText(/An identical paper already exists/)).toBeTruthy();
    expect(wrote(calls)).toEqual(["/api/v1/papers"]);

    calls = stubApi(routes({ "POST /api/v1/branches/b1/exam-plans": fail(422, { code: "PAPER_NOT_AVAILABLE", message: "A paper in the plan is not available." }) }));
    fireEvent.click(screen.getByRole("button", { name: "Schedule & generate now" }));
    expect(await screen.findByText(/The test was not created\. These papers were already built and are listed under Papers: Physics \(v1\)/)).toBeTruthy();
    expect(calls.some((c) => c.path === "/api/v1/exam-plans/plan1/generate")).toBe(false);
  });
});
