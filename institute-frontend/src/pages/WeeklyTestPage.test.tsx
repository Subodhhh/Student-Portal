import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi, type Call } from "../test/stubApi";

const SCOPE = { provider_id: "p1", provider_name: "Alpha Academy", branches: [
  { branch_id: "b1", name: "Pune", whole_branch_allowed: true, batches: [{ batch_id: "ba", name: "NEET A" }], subjects: [{ subject_id: "phy", name: "Physics" }] }] };
const SERIES = { series_id: "s1", name: "Saturday Physics", description: null, status: "ACTIVE", month: "2030-01", weekdays: [5], start_time: "10:00",
  timezone: "Asia/Kolkata", window_minutes: 120, duration_minutes: 60, generate_lead_minutes: 60, marking_mode: "UNIFORM", marks_correct: 4, marks_wrong: 1,
  branches: ["Pune"], planned_count: 4, scheduled_count: 4, missing_occurrences: [], states: { NOT_PUBLISHED: 4 }, last_generated_at: null,
  last_generation_error: null, created_at: "2029-12-01T00:00:00Z", cancelled_at: null, cancel_reason: null };
const TEST = { plan_id: "pl1", test_type: "WEEKLY", series_id: "s1", global_test_id: null, exam_id: null, exam_version_id: null, name: "Saturday Physics",
  state: "NOT_PUBLISHED", exam_status: null, delivery_status: null, error: null, provider_name: "Alpha Academy", home_branch: "Pune",
  start_at: "2030-01-05T04:30:00Z", end_at: "2030-01-05T06:30:00Z", duration_minutes: 60, subjects: ["Physics"],
  audience: { type: "TARGETED", branches: [], batches: ["NEET A"], students: 0 }, assigned_students: 0, question_count: null, total_marks: null, marking: null, created_at: "" };
const PREVIEW = { ok: true, eligible_count: 12, problems: [], conflicts: [],
  warnings: [{ field: "subjects", value: "phy", message: "'Physics Set' has no published version yet: occurrences generated before one is approved leave this subject out." }],
  occurrences: ["05", "12", "19", "26"].map((d) => ({ date: `2030-01-${d}`, start_at: `2030-01-${d}T04:30:00Z`, end_at: `2030-01-${d}T06:30:00Z` })) };

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/api/v1/auth/me": me("BRANCH_ADMIN", ["test.create", "exam.read", "paper.read"]), "/api/v1/notifications/unread-count": { unread_count: 0 },
    "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha Academy", status: "ACTIVE", email: "a@x.io" }]),
    "/api/v1/providers/p1/branches": page([{ branch_id: "b1", name: "Pune" }]),
    "/api/v1/tests/scope": SCOPE, "/api/v1/tests/students": { items: [], total: 0 },
    "/api/v1/tests/papers": { items: [{ paper_id: "pp1", title: "Physics Set", scope: "PROVIDER", source: "Own", published_version: null }], total: 1 },
    "POST /api/v1/test-series/preview": PREVIEW,
    "POST /api/v1/test-series": { ...SERIES, tests: [TEST], generation: { created: ["pl1"], existing: [], skipped_past: [] } },
    "/api/v1/test-series": page([SERIES]), "/api/v1/test-series/s1": { ...SERIES, tests: [TEST] },
    ...extra,
  };
}
const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
afterEach(() => vi.unstubAllGlobals());

async function fillForm() {
  renderApp("/tests/weekly/new");
  fireEvent.click(await screen.findByLabelText("Pune"));
  fireEvent.click(await screen.findByLabelText("NEET A"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Saturday Physics" } });
  fireEvent.click(screen.getByLabelText("Physics"));
  fireEvent.change(await screen.findByLabelText("Paper for Physics"), { target: { value: "pp1" } });
  fireEvent.change(screen.getByLabelText("Month"), { target: { value: "2030-01" } });
  fireEvent.change(screen.getByLabelText("Time zone"), { target: { value: "Asia/Kolkata" } });
}

describe("Weekly Test", () => {
  it("lists Weekly Tests with how many of their dates are scheduled", async () => {
    stubApi(routes());
    renderApp("/tests/weekly");
    expect(await screen.findByRole("link", { name: "Saturday Physics" })).toBeTruthy();
    expect(screen.getByText("4 of 4")).toBeTruthy();
    expect(screen.getByText("Sat at 10:00 (Asia/Kolkata)")).toBeTruthy();
    expect(screen.getByRole("link", { name: "New Weekly Test" })).toBeTruthy();
  });

  it("previews the server's dates and warnings, then creates the series once with an idempotency key", async () => {
    const calls = stubApi(routes());
    await fillForm();
    expect((screen.getByRole("button", { name: "Create Weekly Test" }) as HTMLButtonElement).disabled).toBe(true);    // preview first
    fireEvent.click(screen.getByRole("button", { name: "Preview dates" }));
    expect(await screen.findByText(/has no published version yet/)).toBeTruthy();
    expect(within(screen.getAllByRole("table").at(-1)!).getAllByRole("row")).toHaveLength(5);                       // header + 4 dates
    const body = posts(calls, "/api/v1/test-series/preview")[0].body;
    expect(body).toMatchObject({ month: "2030-01", weekdays: [5], start_time: "10:00", timezone: "Asia/Kolkata", window_minutes: 120, duration_minutes: 60,
      subjects: [{ subject_id: "phy", paper_id: "pp1" }], audience: { provider_id: "p1", branch_ids: ["b1"], batch_ids: ["ba"] } });
    fireEvent.click(screen.getByRole("button", { name: "Create Weekly Test" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Saturday Physics" })).toBeTruthy();                // the detail page
    const create = posts(calls, "/api/v1/test-series");
    expect(create).toHaveLength(1);
    expect(create[0].headers["Idempotency-Key"]).toBeTruthy();
  }, 15000);                                                                                // a long form: slower when the whole suite runs

  it("does not offer creation while the preview reports a problem", async () => {
    stubApi(routes({ "POST /api/v1/test-series/preview": { ...PREVIEW, ok: false, occurrences: [], warnings: [],
      problems: [{ field: "month", value: "2030-01", message: "No occurrence of the chosen weekdays is still in the future in this month." }] } }));
    await fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Preview dates" }));
    expect(await screen.findByText(/No occurrence of the chosen weekdays/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Create Weekly Test" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a recorded generation failure and retries only the missing tests", async () => {
    let fixed = false;
    const calls = stubApi(routes({
      "/api/v1/test-series/s1": () => ({ ...SERIES, scheduled_count: fixed ? 4 : 1, missing_occurrences: fixed ? [] : ["a", "b", "c"],
        last_generation_error: fixed ? null : "RuntimeError: database went away", tests: [TEST] }),
      "POST /api/v1/test-series/s1/generate": () => { fixed = true; return { ...SERIES, tests: [TEST], generation: { created: ["x", "y", "z"], existing: ["pl1"] } }; },
    }));
    renderApp("/tests/weekly/s1");
    expect(await screen.findByText(/Scheduling stopped with an error: RuntimeError: database went away/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Generate missing tests" }));
    expect(await screen.findByText("Scheduled 3 missing test(s).")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(/Scheduling stopped with an error/)).toBeNull());
    expect(posts(calls, "/api/v1/test-series/s1/generate")).toHaveLength(1);
  });

  it("requires a reason to cancel and says what stays unchanged", async () => {
    const calls = stubApi(routes({ "POST /api/v1/test-series/s1/cancel": (_u: URL, init: RequestInit) =>
      (JSON.parse(String(init.body)).reason.length < 5 ? fail(422, { code: "REASON_REQUIRED", message: "A reason of at least 5 characters is required." }) : { stopped: 3 }) }));
    renderApp("/tests/weekly/s1");
    fireEvent.click(await screen.findByRole("button", { name: "Cancel remaining tests" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Exams already generated, attempts and results are not changed/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel remaining tests" }));
    expect(await within(dialog).findByText(/at least 5 characters/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText("Reason (required)"), { target: { value: "Holiday month" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel remaining tests" }));
    expect(await screen.findByText(/3 test\(s\) that had not been generated were stopped/)).toBeTruthy();
    expect(posts(calls, "/api/v1/test-series/s1/cancel").at(-1)?.body).toEqual({ reason: "Holiday month" });
  });

  it("lets teachers see Weekly Tests but not create or change them", async () => {
    stubApi(routes({ "/api/v1/auth/me": me("TEACHER", ["exam.read", "paper.read"]) }));
    renderApp("/tests/weekly/s1");
    expect(await screen.findByRole("heading", { level: 2, name: "Saturday Physics" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Generate missing tests" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel remaining tests" })).toBeNull();
  });
});
