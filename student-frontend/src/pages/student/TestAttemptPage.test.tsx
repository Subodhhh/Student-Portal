import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import TestAttemptPage from "./TestAttemptPage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const q = (id: string, subject: string) => ({
  question_id: id, question_order: 1, subject, question_text: `${subject} question ${id}`, question_type: "MCQ",
  option_a: "a", option_b: "b", option_c: "c", option_d: "d", marks: 4, negative_marks: 1, image_reference: null });

const globalTest = {
  test_id: "t1", name: "NEET UG - 01(testing)", description: null, duration_minutes: 180, total_questions: 2, total_marks: 8,
  start_at: null, end_at: null, status: "ACTIVE", global_occurrence_id: "occ-1",
  provider: { provider_id: "p", name: "Anchor Academy" }, branch: { branch_id: "b", name: "Anchor Branch", city: null, state: null },
  sections: [
    { position: 3, subject: "Biology", status: "NO_SUBMISSION", message: "Your institute has not submitted a Biology paper for this test.", question_count: 0 },
    { position: 1, subject: "Physics", status: "PAPER", message: null, question_count: 1 },
    { position: 2, subject: "Chemistry", status: "PAPER", message: null, question_count: 1 },
  ],
  questions: [q("q2", "Physics"), q("q1", "Chemistry")],   // the server's per-student order
};

const state = (warnings: number, extra: Record<string, unknown> = {}) => ({
  attempt_id: "a1", status: "IN_PROGRESS", warnings, threshold: 3, submit_reason: null, review_status: null, review: null, ...extra });

type Call = { url: string; method: string; body: unknown };

function renderAttempt(events: (n: number) => unknown = (n) => state(n)) {
  const expires = new Date(Date.now() + 3600_000).toISOString();
  const calls: Call[] = [];
  let reported = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.endsWith("/tests/t1/start")) return json(200, globalTest);
    if (url.endsWith("/profile")) return json(200, { first_name: "A", last_name: "B" });
    if (url.endsWith("/attempts/start")) return json(200, { attempt_id: "a1", test_id: "t1", started_at: expires, expires_at: expires, status: "IN_PROGRESS" });
    if (url.endsWith("/attempts/a1/answers")) return json(200, []);
    if (url.endsWith("/attempts/a1/integrity")) return json(200, state(0));
    if (url.endsWith("/attempts/a1/integrity-events")) return json(200, events(++reported));
    if (url.endsWith("/attempts/a1/review")) return json(200, { status: "OPEN", student_message: "x", decision_reason: null });
    return json(200, {});
  }));
  render(<MemoryRouter initialEntries={["/tests/t1/attempt"]}><Routes><Route path="/tests/:testId/attempt" element={<TestAttemptPage />} /></Routes></MemoryRouter>);
  return calls;
}

async function begin() {
  fireEvent.click(await screen.findByRole("button", { name: "I understand, start in fullscreen" }));
  await screen.findByText("NEET UG - 01(testing)");
}

function hide() {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
}

afterEach(() => vi.unstubAllGlobals());

describe("Global Test attempt", () => {
  it("shows every configured subject as a tab in order, the canonical title, and why a subject has no paper", async () => {
    renderAttempt();
    await begin();
    expect(screen.getByText("Global Test")).toBeTruthy();
    expect(screen.queryByText("Anchor Academy")).toBeNull();
    const tabs = screen.getAllByRole("button").map((b) => b.textContent).filter((t) => ["All Sections", "Physics", "Chemistry", "Biology"].includes(t ?? ""));
    expect(tabs).toEqual(["All Sections", "Physics", "Chemistry", "Biology"]);
    expect(screen.getByText("Physics question q2")).toBeTruthy();         // the server's order: first question first
    fireEvent.click(screen.getByRole("button", { name: "Biology" }));
    expect(screen.getByText("Your institute has not submitted a Biology paper for this test.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Chemistry" }));
    expect(screen.getByText("Chemistry question q1")).toBeTruthy();
  });
});

describe("Exam integrity", () => {
  it("explains the rules first and starts nothing (no attempt, no timer) until the student accepts them", async () => {
    const calls = renderAttempt();
    expect(await screen.findByText("Before you begin")).toBeTruthy();
    expect(screen.getByText(/not a finding of cheating/)).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith("/attempts/start"))).toBe(false);
    await begin();
    expect(calls.filter((c) => c.url.endsWith("/attempts/start"))).toHaveLength(1);
  });

  it("reports leaving the test and shows the server's warning count", async () => {
    const calls = renderAttempt();
    await begin();
    expect(await screen.findByText(/Warnings: 0 \/ 3/)).toBeTruthy();
    await act(async () => hide());
    expect(await screen.findByText(/Warnings: 1 \/ 3/)).toBeTruthy();
    const sent = calls.filter((c) => c.url.endsWith("/integrity-events"));
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toMatchObject({ kind: "TAB_HIDDEN" });
    expect((sent[0].body as { client_event_id: string }).client_event_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("never counts the page's own submit dialog or a reload as leaving", async () => {
    const calls = renderAttempt();
    await begin();
    vi.spyOn(window, "confirm").mockImplementation(() => {
      window.dispatchEvent(new Event("blur"));                          // the native dialog takes focus from the window
      return false;
    });
    fireEvent.click(screen.getByRole("button", { name: /Submit Test/ }));
    window.dispatchEvent(new Event("beforeunload"));                    // a reload
    await act(async () => hide());
    expect(calls.filter((c) => c.url.endsWith("/integrity-events"))).toHaveLength(0);
  });

  it("shows the automatic submission, its reason, and lets the student ask the institute for a review", async () => {
    const calls = renderAttempt(() => state(3, { status: "AUTO_SUBMITTED", submit_reason: "WARNING_THRESHOLD", review_status: "UNDER_REVIEW", auto_submitted: true }));
    await begin();
    await act(async () => hide());
    expect(await screen.findByText("Test submitted automatically")).toBeTruthy();
    expect(screen.getByText(/because 3 warnings were recorded/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/tell your institute what happened/), { target: { value: "A system update prompt took focus." } });
    fireEvent.click(screen.getByRole("button", { name: "Request a review" }));
    await waitFor(() => expect(screen.getByText(/Review requested/)).toBeTruthy());
    expect(calls.find((c) => c.url.endsWith("/attempts/a1/review"))?.body).toEqual({ message: "A system update prompt took focus." });
  });
});
