import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi, type Call } from "../test/stubApi";

const TEST = { test_id: "gt1", name: "National Mock #3", description: null, exam_type: null, target_class: null, duration_minutes: 60, status: "PUBLISHED",
  derived_from_test_id: null, created_at: "2026-10-10T00:00:00Z", published_at: "2026-10-11T00:00:00Z", publisher: "Platform", question_count: 20, total_marks: "80.00",
  sections: [{ position: 1, subject_id: "phy", subject: "Physics", paper_id: "gp", paper_title: "Platform Physics", paper_version_id: "gpv", version_no: 1,
    version_status: "PUBLISHED", question_count: 20, total_marks: "80.00" }], kind: "SECTIONS", subjects: [], marking_mode: "PER_QUESTION", marks_correct: null, marks_wrong: null, generate_lead_minutes: 60, occurrences: {}, next_occurrence_at: null };
const contribution = (over: Record<string, unknown> = {}) => ({ contribution_id: "c1", test_id: "gt1", test_name: "National Mock #3", test_status: "PUBLISHED",
  subject_id: "chem", subject: "Chemistry", provider_name: "Alpha Academy", branch_name: "Pune", paper_title: "Alpha Chemistry", paper_version_id: "pv1", version_no: 2,
  version_status: "PUBLISHED", question_count: 30, total_marks: "120.00", note: null, status: "PENDING", submitted_by_name: "Asha", submitted_at: "2026-10-12T00:00:00Z",
  reviewed_by_name: null, reviewed_at: null, review_note: null, copy_status: null, copy_error: null, global_paper_id: null, global_paper_version_id: null,
  global_version_status: null, competing: null, section_exists: null, can_withdraw: false, ...over });
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/global-tests": page([TEST]) };
const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
afterEach(() => vi.unstubAllGlobals());

describe("Global Tests for provider staff", () => {
  it("offers one of the provider's approved papers for a published test", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["paper.read", "paper.publish", "exam.read"]),
      "/api/v1/global-tests/gt1/contribution-options": { items: [{ paper_id: "p1", paper_title: "Alpha Chemistry", subject: "Chemistry", paper_version_id: "pv1",
        version_no: 2, version_status: "PUBLISHED", question_count: 30, total_marks: "120.00" }], total: 1 },
      "POST /api/v1/global-tests/gt1/contributions": contribution({ can_withdraw: true }),
      "/api/v1/global-test-contributions": page([contribution({ can_withdraw: true })]) });
    renderApp("/tests/global?tab=tests");
    fireEvent.click(await screen.findByRole("button", { name: "Offer a paper" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/The published test itself never changes/)).toBeTruthy();
    fireEvent.change(await within(dialog).findByLabelText("Paper"), { target: { value: "pv1" } });
    fireEvent.change(within(dialog).getByLabelText("Note for the reviewer (optional)"), { target: { value: "New for this term" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Offer paper" }));
    expect(await screen.findByText(/Offered “Alpha Chemistry” \(Chemistry\) for National Mock #3/)).toBeTruthy();
    expect(posts(calls, "/api/v1/global-tests/gt1/contributions")[0].body).toEqual({ paper_version_id: "pv1", note: "New for this term" });
    expect(await screen.findByRole("button", { name: "Withdraw" })).toBeTruthy();                          // own pending contribution
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByText(/Conflict/)).toBeNull();
  });

  it("shows an honest empty state when nothing may be offered", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("BRANCH_ADMIN", ["paper.read", "paper.publish", "exam.read"]),
      "/api/v1/global-tests/gt1/contribution-options": { items: [], total: 0 } });
    renderApp("/tests/global?tab=tests");
    fireEvent.click(await screen.findByRole("button", { name: "Offer a paper" }));
    expect(await screen.findByText("No paper available to offer")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Offer paper" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Global Test contributions for the platform", () => {
  it("shows conflicts and approves explicitly, explaining what the approval does", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage", "paper.publish", "exam.read"]),
      "/api/v1/global-test-contributions": page([contribution({ competing: 2 }), contribution({ contribution_id: "c2", provider_name: "Beta Classes", paper_title: "Beta Chemistry", competing: 2 })]),
      "POST /api/v1/global-test-contributions/c1/approve": contribution({ status: "APPROVED", copy_status: "COPIED", global_paper_version_id: "gv1", global_version_status: "VALIDATED" }) });
    renderApp("/tests/global?tab=contributions");
    expect(await screen.findAllByText("Conflict: 2 offers")).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/approving this one blocks theirs until you reject them/)).toBeTruthy();
    expect(within(dialog).getByText(/The published test does not change/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/now needs the normal paper approval/)).toBeTruthy();
    expect(posts(calls, "/api/v1/global-test-contributions/c1/approve")).toHaveLength(1);
  });

  it("reports a conflict refused by the server and requires a reason to reject", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage"]),
      "/api/v1/global-test-contributions": page([contribution({ competing: 2 })]),
      "POST /api/v1/global-test-contributions/c1/approve": fail(409, { code: "SUBJECT_CONFLICT", message: "Another contribution for this subject is already approved." }),
      "POST /api/v1/global-test-contributions/c1/reject": (_u: URL, init: RequestInit) => (JSON.parse(String(init.body)).reason.length < 5
        ? fail(422, { code: "REASON_REQUIRED", message: "A reason of at least 5 characters is required." }) : contribution({ status: "REJECTED" })) });
    renderApp("/tests/global?tab=contributions");
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Another contribution for this subject is already approved.")).toBeTruthy();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(await within(dialog).findByText(/at least 5 characters/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText("Reason (required)"), { target: { value: "Chemistry already covered" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Rejected “Alpha Chemistry”.")).toBeTruthy();
    expect(posts(calls, "/api/v1/global-test-contributions/c1/reject").at(-1)?.body).toEqual({ reason: "Chemistry already covered" });
  });

  it("surfaces a failed copy and retries it", async () => {
    let copied = false;
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["examplan.manage"]),
      "/api/v1/global-test-contributions": () => page([contribution(copied
        ? { status: "APPROVED", copy_status: "COPIED", global_paper_version_id: "gv1", global_version_status: "VALIDATED" }
        : { status: "APPROVED", copy_status: "FAILED", copy_error: "PAPER_SERVICE_UNAVAILABLE: The paper service is unavailable." })]),
      "POST /api/v1/global-test-contributions/c1/retry-copy": () => { copied = true; return contribution({ status: "APPROVED", copy_status: "COPIED" }); } });
    renderApp("/tests/global?tab=contributions");
    fireEvent.change(await screen.findByLabelText("Status"), { target: { value: "" } });
    expect(await screen.findByText("Copy failed")).toBeTruthy();
    expect(screen.getByText(/PAPER_SERVICE_UNAVAILABLE/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry copy" }));
    expect(await screen.findByRole("link", { name: "Review platform copy" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Copy failed")).toBeNull());
    expect(posts(calls, "/api/v1/global-test-contributions/c1/retry-copy")).toHaveLength(1);
  });
});

describe("Global Test detail workflow", () => {
  const SCHEDULE = { plan_id: "pl9", test_type: "GLOBAL", series_id: null, global_test_id: "gt1", exam_id: null, exam_version_id: null, name: "National Mock #3",
    state: "NOT_PUBLISHED", exam_status: null, delivery_status: null, error: null, provider_name: "Alpha Academy", home_branch: "Pune",
    start_at: "2030-01-05T04:30:00Z", end_at: "2030-01-05T06:30:00Z", duration_minutes: 60, subjects: ["Physics"],
    audience: { type: "BATCHES", branches: [], batches: ["NEET A"], students: 0 }, assigned_students: 0, question_count: null, total_marks: null, marking: null, created_at: "" };
  const detailRoutes = (role: string, perms: string[], extra: Record<string, unknown> = {}) => ({ ...common, "/api/v1/auth/me": me(role, perms),
    "/api/v1/global-tests/gt1": TEST, "/api/v1/tests": page([SCHEDULE]), "/api/v1/global-test-contributions": page([contribution({ competing: 1 })]), ...extra });

  it("gives the platform the configuration, version, contribution and scheduling steps in one place", async () => {
    const calls = stubApi(detailRoutes("SUPER_ADMIN", ["examplan.manage", "exam.generate", "paper.publish", "exam.read"], {
      "POST /api/v1/global-tests/gt1/derive": { ...TEST, test_id: "gt2", name: "National Mock #3 (copy)", status: "DRAFT", derived_from_test_id: "gt1" },
      "/api/v1/global-tests/gt2": { ...TEST, test_id: "gt2", name: "National Mock #3 (copy)", status: "DRAFT", derived_from_test_id: "gt1" },
      "POST /api/v1/exam-plans/pl9/generate": { job_id: "j1", status: "SUCCEEDED", exam_id: "ex9", error: null } }));
    renderApp("/tests/global/gt1");
    expect(await screen.findByRole("heading", { level: 2, name: "National Mock #3" })).toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Global Tests" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "1. Configuration" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Schedule this Global Test" }).getAttribute("href")).toBe("/tests/global/gt1/schedule");
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeTruthy();
    expect(await screen.findByText("Alpha Chemistry v2")).toBeTruthy();                                   // contributions for this test
    expect(calls.some((c) => c.path === "/api/v1/global-test-contributions" && c.search.get("test_id") === "gt1")).toBe(true);
    expect(calls.some((c) => c.path === "/api/v1/tests" && c.search.get("global_test_id") === "gt1")).toBe(true);
    fireEvent.click(await screen.findByRole("button", { name: "Generate now" }));
    expect(await screen.findByText("Generation succeeded.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create new version" }));
    expect(await screen.findByRole("heading", { level: 2, name: "National Mock #3 (copy)" })).toBeTruthy();   // the new draft version
    expect(await screen.findByRole("button", { name: "Edit draft" })).toBeTruthy();
    expect(screen.getByText(/A new version of/)).toBeTruthy();
  });

  it("shows provider admins scheduling and offering, but no platform actions", async () => {
    stubApi(detailRoutes("PROVIDER_ADMIN", ["examplan.manage", "exam.generate", "paper.publish", "exam.read"]));
    renderApp("/tests/global/gt1");
    expect(await screen.findByRole("link", { name: "Schedule this Global Test" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Offer a paper" })).toBeTruthy();
    for (const name of ["Withdraw", "Publish", "Edit draft", "Create new version", "Approve", "Reject"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("lets teachers offer papers but not schedule or manage the test", async () => {
    stubApi(detailRoutes("TEACHER", ["paper.publish", "paper.read", "exam.read"]));
    renderApp("/tests/global/gt1");
    expect(await screen.findByRole("button", { name: "Offer a paper" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Schedule this Global Test" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Generate now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
  });

  it("keeps the schedule page away from roles without examplan.manage", async () => {
    stubApi(detailRoutes("TEACHER", ["paper.publish", "paper.read", "exam.read"]));
    renderApp("/tests/global/gt1/schedule");
    expect(await screen.findByText("Access denied")).toBeTruthy();
  });
});
