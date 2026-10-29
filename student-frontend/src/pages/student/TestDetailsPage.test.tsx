import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import TestDetailsPage from "./TestDetailsPage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const test = (id: string, provider: string, branch: string, extra: Record<string, unknown> = {}) => ({
  test_id: id, name: "NEET UG - 02 (2026-11-07)", description: null, duration_minutes: 180, total_questions: 90, total_marks: 360,
  start_at: "2026-11-07T04:30:00+00:00", end_at: "2099-11-07T10:30:00+00:00", status: "ACTIVE",
  provider: { provider_id: `p-${id}`, name: provider }, branch: { branch_id: `b-${id}`, name: branch, city: null, state: null }, ...extra });

function renderDetails(id: string, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.endsWith(`/tests/${id}/details`) ? json(200, body) : json(404, { detail: "Not found" }))));
  render(<MemoryRouter initialEntries={[`/tests/${id}`]}><Routes><Route path="/tests/:testId" element={<TestDetailsPage />} /></Routes></MemoryRouter>);
}

afterEach(() => vi.unstubAllGlobals());

describe("Test details", () => {
  it("shows a unified Global Test as the Global Test, never as its anchor provider or branch", async () => {
    renderDetails("t1", test("t1", "Anchor Academy", "Mumbai Branch", { global_occurrence_id: "occ-1" }));
    expect(await screen.findByText("Global Test")).toBeTruthy();
    expect(screen.getByText("Global Test (papers from your institutes)")).toBeTruthy();
    expect(screen.queryByText("Anchor Academy")).toBeNull();
    expect(screen.queryByText(/Mumbai Branch/)).toBeNull();
  });

  const sections = [
    { position: 2, subject: "Chemistry", status: "PAPER", message: null, question_count: 45 },
    { position: 1, subject: "Physics", status: "PAPER", message: null, question_count: 45 },
    { position: 3, subject: "Biology", status: "NOT_ENROLLED", message: "You are not enrolled in Biology with any of your institutes.", question_count: 0 },
  ];

  it("lists every configured subject in order, with the reason for one without a paper", async () => {
    renderDetails("t3", test("t3", "Anchor", "B", { name: "NEET UG - 01(testing)", global_occurrence_id: "occ-1", start_at: "2020-01-01T00:00:00+00:00", sections }));
    expect(await screen.findByText("NEET UG - 01(testing)")).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Physics45 question(s)", "Chemistry45 question(s)", "BiologyYou are not enrolled in Biology with any of your institutes."]);
    expect(screen.getByText("Take Test")).toBeTruthy();
  });

  it("never offers to start a card without any paper", async () => {
    const none = sections.map((x) => ({ ...x, status: "NO_SUBMISSION", message: `No ${x.subject} paper.`, question_count: 0 }));
    renderDetails("t4", test("t4", "Anchor", "B", { global_occurrence_id: "occ-1", total_questions: 0, start_at: "2020-01-01T00:00:00+00:00", sections: none }));
    expect(await screen.findByText(/cannot be started/)).toBeTruthy();
    expect(screen.queryByText("Take Test")).toBeNull();
  });

  it("keeps an ordinary test's provider and branch", async () => {
    renderDetails("t2", test("t2", "Apex NEET Academy", "Thane Branch"));
    expect((await screen.findAllByText("Apex NEET Academy")).length).toBe(2);
    expect(screen.getByText(/Thane Branch/)).toBeTruthy();
    expect(screen.queryByText("Global Test")).toBeNull();
  });
});
