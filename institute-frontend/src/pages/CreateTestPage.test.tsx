import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { visibleNav } from "../auth/nav";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi, type Call } from "../test/stubApi";

const PROVIDER = { provider_id: "p1", name: "Alpha Academy", status: "ACTIVE", email: "a@x.io" };
const SCOPE = {
  provider_id: "p1", provider_name: "Alpha Academy",
  branches: [
    { branch_id: "b1", name: "Pune", whole_branch_allowed: true, batches: [{ batch_id: "ba", name: "NEET A" }, { batch_id: "bb", name: "NEET B" }],
      subjects: [{ subject_id: "phy", name: "Physics" }, { subject_id: "chem", name: "Chemistry" }] },
    { branch_id: "b2", name: "Mumbai", whole_branch_allowed: true, batches: [{ batch_id: "bc", name: "JEE C" }], subjects: [{ subject_id: "phy", name: "Physics" }] },
  ],
};
const PUBLISHED = { plan_id: "plan1", test_type: "DAILY", series_id: null, global_test_id: null, exam_id: "ex1", exam_version_id: "ev1", name: "Weekly Physics", state: "PUBLISHED_DELIVERY_PENDING", exam_status: "PUBLISHED",
  delivery_status: "PENDING", error: null, provider_name: "Alpha Academy", home_branch: "Pune", start_at: "2030-01-01T09:00:00Z", end_at: "2030-01-01T12:00:00Z",
  duration_minutes: 60, subjects: ["Physics"], audience: { type: "TARGETED", branches: [], batches: ["NEET A"], students: 0 }, assigned_students: 2,
  question_count: 3, total_marks: "12.00", marking: { mode: "UNIFORM", correct: 4, wrong: 1 }, created_at: "2029-12-31T00:00:00Z" };
let pvStatus = "VALIDATED";

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/api/v1/auth/me": me("PROVIDER_ADMIN", ["test.create", "paper.read", "exam.read"]), "/api/v1/notifications/unread-count": { unread_count: 0 },
    "/api/v1/providers": page([PROVIDER]), "/api/v1/providers/p1/branches": page([{ branch_id: "b1", name: "Pune" }]),
    "/api/v1/tests/scope": SCOPE,
    "/api/v1/tests/students": { items: [{ student_id: "STU000003", name: "Riya Patil", branches: ["Pune"], batches: ["NEET B"] }], total: 1 },
    "POST /api/v1/tests/audience": { eligible_count: 2, students: [{ student_id: "STU000001", name: "Aman Shah" }, { student_id: "STU000002", name: "Neel Rao" }], not_assigned: [] },
    "POST /api/v1/tests/check": { ok: true, eligible_count: 2 },
    "POST /api/v1/branches/b1/subjects/phy/papers/upload": { paper_version_id: "pv1", question_count: 3 },
    "/api/v1/paper-versions/pv1": () => ({ latest_version: { status: pvStatus, question_count: 3, total_marks: "12.00" } }),
    "POST /api/v1/paper-versions/pv1/approve": () => { pvStatus = "APPROVED"; return { status: "APPROVED" }; },
    "POST /api/v1/paper-versions/pv1/publish": () => { pvStatus = "PUBLISHED"; return { status: "PUBLISHED" }; },
    "POST /api/v1/tests": PUBLISHED,
    "/api/v1/tests": page([PUBLISHED]),
    ...extra,
  };
}
const next = () => fireEvent.click(screen.getByRole("button", { name: "Next" }));
const file = (name: string, text = "x") => new File([text], name, { type: "text/csv" });
const post = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
afterEach(() => { vi.unstubAllGlobals(); pvStatus = "VALIDATED"; });

async function throughFiles() {
  renderApp("/tests/new");
  const steps = within(await screen.findByRole("list", { name: "Steps" })).getAllByRole("listitem").map((li) => li.textContent);
  expect(steps).toEqual(["1. Audience", "2. Test name", "3. Subjects", "4. Details & schedule", "5. Question papers", "6. Import & validate", "7. Review & publish"]);
  expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);    // audience first
  fireEvent.click(screen.getByLabelText("Pune"));
  fireEvent.click(await screen.findByLabelText("NEET A"));
  next();
  expect(await screen.findByLabelText("Test name")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Test name"), { target: { value: "Weekly Physics" } });
  next();
  fireEvent.click(await screen.findByLabelText("Physics"));
  next();
  expect(await screen.findByLabelText("Duration (minutes)")).toBeTruthy();
  next();
  expect(await screen.findByLabelText("Question paper")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);    // both files required
  fireEvent.change(screen.getByLabelText("Question paper"), { target: { files: [file("paper.csv")] } });
  expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Answer key"), { target: { files: [file("key.csv")] } });
  next();
  expect(await screen.findByRole("button", { name: "Import & Validate" })).toBeTruthy();
}

describe("Test sidebar", () => {
  it("offers Daily and Weekly Tests to every role holding test.create, and only the scheduled tests to teachers", () => {
    for (const role of ["SUPER_ADMIN", "ADMIN", "PROVIDER_ADMIN", "BRANCH_ADMIN"] as const) {
      const items = visibleNav(["test.create"], role).filter((i) => i.section === "Test").map((i) => i.label);
      expect(items).toEqual(["Daily Test", "Weekly Test", "Scheduled Tests"]);
    }
    expect(visibleNav(["exam.read", "paper.read"], "TEACHER").filter((i) => i.section === "Test").map((i) => i.label)).toEqual(["Scheduled Tests"]);
    expect(visibleNav(["paper.read"], "TEACHER").some((i) => i.section === "Test")).toBe(false);
  });
});

describe("Create Test wizard", () => {
  it("runs audience -> name -> subjects -> details -> files -> import & validate -> review -> publish, with the server deciding each gate", async () => {
    const calls = stubApi(routes());
    await throughFiles();
    expect(post(calls, "/api/v1/tests/audience")[0].body).toEqual({ provider_id: "p1", branch_ids: ["b1"], whole_branch_ids: [], batch_ids: ["ba"], student_ids: [], target_class: null });
    expect(post(calls, "/api/v1/tests/check")).toHaveLength(1);
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);    // not validated yet
    fireEvent.click(screen.getByRole("button", { name: "Import & Validate" }));
    expect(await screen.findByText("Passed")).toBeTruthy();
    expect(post(calls, "/api/v1/branches/b1/subjects/phy/papers/upload")[0].body).toMatchObject({ file: "paper.csv", answer_key: "key.csv", title: "Weekly Physics – Physics" });
    next();
    expect(await screen.findByRole("heading", { name: "Review & publish" })).toBeTruthy();
    expect(screen.getByText("+4 correct, −1 wrong, 0 unanswered")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();                                                       // 3 questions x 4
    fireEvent.click(screen.getByRole("button", { name: "Publish test" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Publish" }));
    expect(await screen.findByText("Daily Test published")).toBeTruthy();
    const order = calls.filter((c) => c.method === "POST").map((c) => c.path);
    expect(order.indexOf("/api/v1/paper-versions/pv1/approve")).toBeLessThan(order.indexOf("/api/v1/paper-versions/pv1/publish"));
    expect(order.indexOf("/api/v1/paper-versions/pv1/publish")).toBeLessThan(order.indexOf("/api/v1/tests"));
    const pub = post(calls, "/api/v1/tests")[0];
    expect(pub.headers["Idempotency-Key"]).toBeTruthy();
    expect(pub.body).toMatchObject({ name: "Weekly Physics", subjects: [{ subject_id: "phy", paper_version_id: "pv1" }], marking_mode: "UNIFORM", marks_correct: 4, marks_wrong: 1 });
    expect(String((pub.body as { start_at: string }).start_at)).toMatch(/Z$/);                           // stored in UTC
  });

  it("shows the validation report for rejected files and only lets corrected files through", async () => {
    let attempt = 0;
    const calls = stubApi(routes({
      "POST /api/v1/branches/b1/subjects/phy/papers/upload": () => (++attempt === 1
        ? fail(422, { code: "EXTRACTION_FAILED", message: "The paper was rejected as a whole: more than five questions failed validation.",
          summary: { questions_detected: 10, questions_valid: 4, questions_failed: 6, threshold_exceeded: true, critical: false },
          errors: [{ row: 5, field: "option_c", message: "Option C is empty.", question: "M004" }, { row: 7, field: "correct", message: "The answer key has no answer for question M006.", question: "M006" }] })
        : { paper_version_id: "pv1", question_count: 3 }),
    }));
    await throughFiles();
    fireEvent.click(screen.getByRole("button", { name: "Import & Validate" }));
    expect(await screen.findByText("Rejected")).toBeTruthy();
    expect(screen.getByText(/more than five failed: whole upload rejected/)).toBeTruthy();
    const table = screen.getByRole("table");
    expect(within(table).getByText("M004")).toBeTruthy();
    expect(within(table).getByText("Option C is empty.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download the validation report" }).getAttribute("href")).toContain("M006");
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);    // cannot proceed to publishing
    fireEvent.click(screen.getByRole("button", { name: "Back" }));                                      // re-upload corrected files
    fireEvent.change(await screen.findByLabelText("Question paper"), { target: { files: [file("fixed.csv")] } });
    fireEvent.change(screen.getByLabelText("Answer key"), { target: { files: [file("fixed-key.csv")] } });
    next();
    fireEvent.click(await screen.findByRole("button", { name: "Import & Validate" }));
    expect(await screen.findByText("Passed")).toBeTruthy();
    expect(post(calls, "/api/v1/branches/b1/subjects/phy/papers/upload")).toHaveLength(2);
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("reuses an identical, already imported paper instead of duplicating it", async () => {
    stubApi(routes({ "POST /api/v1/branches/b1/subjects/phy/papers/upload": fail(409, { code: "DUPLICATE_PAPER", message: "exists", paper_version_id: "pv1" }) }));
    await throughFiles();
    fireEvent.click(screen.getByRole("button", { name: "Import & Validate" }));
    expect(await screen.findByText(/already imported, reused \(no duplicate\)/)).toBeTruthy();
  });

  it("keeps the user on the audience step when the server refuses a selection", async () => {
    const calls = stubApi(routes({ "POST /api/v1/tests/audience": fail(422, { code: "AUDIENCE_INVALID", message: "Some selections are not allowed.",
      errors: [{ field: "batch_ids", value: "ba", message: "This batch is not in a selected branch available to you." }] }) }));
    renderApp("/tests/new");
    fireEvent.click(await screen.findByLabelText("Pune"));
    fireEvent.click(await screen.findByLabelText("NEET A"));
    next();
    expect(await screen.findByText(/This batch is not in a selected branch available to you/)).toBeTruthy();
    expect(screen.queryByLabelText("Test name")).toBeNull();
    expect(post(calls, "/api/v1/tests/check")).toHaveLength(0);
  });

  it("invalidates batches and students when a branch is deselected, and adds individual students on top of batches", async () => {
    const calls = stubApi(routes());
    renderApp("/tests/new");
    fireEvent.click(await screen.findByLabelText("Mumbai"));
    fireEvent.click(await screen.findByLabelText("JEE C"));
    fireEvent.click(screen.getByLabelText("Pune"));
    fireEvent.click(await screen.findByLabelText("Select STU000003"));
    fireEvent.click(screen.getByLabelText("Mumbai"));                                                   // deselect Mumbai
    expect(screen.queryByLabelText("JEE C")).toBeNull();                                                 // its batch is gone ...
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);    // ... and the students were cleared
    fireEvent.click(screen.getByLabelText("NEET B"));
    fireEvent.click(await screen.findByLabelText("Select STU000003"));
    next();
    await waitFor(() => expect(post(calls, "/api/v1/tests/audience")).toHaveLength(1));
    expect(post(calls, "/api/v1/tests/audience")[0].body).toEqual({ provider_id: "p1", branch_ids: ["b1"], whole_branch_ids: [], batch_ids: ["bb"], student_ids: ["STU000003"], target_class: null });
  });

  it("uses an approved paper from the bank instead of an upload", async () => {
    const calls = stubApi(routes({
      "/api/v1/tests/papers": { items: [{ paper_id: "pp1", title: "Physics Set 4", scope: "PROVIDER", source: "Own",
        published_version: { paper_version_id: "pvbank", version_no: 2, question_count: 5, total_marks: "20.00" } }], total: 1 },
      "/api/v1/paper-versions/pvbank": { latest_version: { status: "PUBLISHED", question_count: 5, total_marks: "20.00" } },
    }));
    renderApp("/tests/new");
    fireEvent.click(await screen.findByLabelText("Pune"));
    fireEvent.click(await screen.findByLabelText("NEET A"));
    next();
    fireEvent.change(await screen.findByLabelText("Test name"), { target: { value: "Bank Physics" } });
    next();
    fireEvent.click(await screen.findByLabelText("Physics"));
    next();
    await screen.findByLabelText("Duration (minutes)");
    next();
    fireEvent.click(await screen.findByLabelText("Use an approved paper"));
    fireEvent.change(await screen.findByLabelText("Approved paper"), { target: { value: "pp1" } });
    expect(await screen.findByText(/approved paper chosen/)).toBeTruthy();
    next();
    fireEvent.click(await screen.findByRole("button", { name: "Import & Validate" }));                 // nothing to import for a bank paper
    expect(await screen.findByText(/in the approved paper chosen from the bank/)).toBeTruthy();
    next();
    fireEvent.click(await screen.findByRole("button", { name: "Publish test" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Publish" }));
    expect(await screen.findByText("Daily Test published")).toBeTruthy();
    expect(calls.some((c) => c.path.includes("/papers/upload"))).toBe(false);
    expect(calls.some((c) => c.path.includes("/paper-versions/pvbank/approve") || c.path.includes("/paper-versions/pvbank/publish"))).toBe(false);
    expect(post(calls, "/api/v1/tests")[0].body).toMatchObject({ subjects: [{ subject_id: "phy", paper_version_id: "pvbank" }] });
  });

  it("reports a failed publication honestly", async () => {
    stubApi(routes({ "POST /api/v1/tests": fail(409, { code: "PUBLICATION_FAILED", message: "Publication refused: not every subject's paper was available." }) }));
    await throughFiles();
    fireEvent.click(screen.getByRole("button", { name: "Import & Validate" }));
    await screen.findByText("Passed");
    next();
    fireEvent.click(await screen.findByRole("button", { name: "Publish test" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Publish" }));
    expect(await screen.findByText(/The test was NOT published/)).toBeTruthy();
    expect(screen.queryByText("Daily Test published")).toBeNull();
  });
});

describe("Scheduled Tests", () => {
  it("lists tests with their state, schedule, subjects and audience", async () => {
    stubApi(routes({ "/api/v1/tests": page([PUBLISHED, { ...PUBLISHED, plan_id: "plan2", exam_id: null, name: "Failed one", state: "PUBLICATION_FAILED", error: "database hiccup" }]) }));
    renderApp("/tests");
    expect(await screen.findByRole("link", { name: "Weekly Physics" })).toBeTruthy();
    expect(screen.getByText("Published, delivery in progress")).toBeTruthy();
    expect(screen.getByText("Not published (failed)")).toBeTruthy();
    expect(screen.getAllByText("batches: NEET A")).toHaveLength(2);
    expect(within(screen.getByRole("table")).getAllByText("Daily")).toHaveLength(2);
  });

  it("filters by test type through the API", async () => {
    const calls = stubApi(routes({ "/api/v1/tests": page([{ ...PUBLISHED, test_type: "WEEKLY", series_id: "s1", name: "Saturday Physics" }]) }));
    renderApp("/tests");
    expect(await screen.findByRole("link", { name: "Weekly Test plan" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "WEEKLY" } });
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/tests" && c.search.get("test_type") === "WEEKLY")).toBe(true));
  });
});
