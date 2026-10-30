import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ScheduledTestsPage from "./ScheduledTestsPage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const test = (id: string, name: string, provider: string, branch: string, extra: Record<string, unknown> = {}) => ({
  test_id: id, name, description: null, duration_minutes: 180, total_questions: 90, total_marks: 360, status: "ACTIVE",
  start_at: "2026-10-24T16:30:00+00:00", end_at: "2099-10-25T15:30:00+00:00",
  provider: { provider_id: `p-${id}`, name: provider }, branch: { branch_id: `b-${id}`, name: branch, city: null, state: null }, ...extra });

afterEach(() => vi.unstubAllGlobals());

describe("Scheduled Tests", () => {
  it("shows a unified Global Test as one Global Test card, never as its anchor provider", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/tests")) return json(200, [
        test("t1", "NEET UG - 02 (2026-11-07)", "Anchor Academy", "Mumbai Branch", { global_occurrence_id: "occ-1" }),
        test("t2", "Weekly Physics", "Apex NEET Academy", "Thane Branch"),
      ]);
      if (url.includes("/attempts/test/")) return json(404, { detail: "Not found" });
      return json(200, []);
    }));
    render(<MemoryRouter><ScheduledTestsPage /></MemoryRouter>);
    expect(await screen.findByText("NEET UG - 02 (2026-11-07)")).toBeTruthy();
    expect(screen.getAllByText("Global Test")).toHaveLength(1);
    expect(screen.queryByText("Anchor Academy")).toBeNull();
    expect(screen.getByText("Apex NEET Academy")).toBeTruthy();                 // ordinary tests keep their provider and branch
  });

  it("sends a Global Test with paper choice whose papers are not chosen yet to the details page instead of starting it", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/tests")) return json(200, [
        test("t1", "NEET UG - 03", "Anchor", "B", { global_occurrence_id: "occ-1", total_questions: null, total_marks: null, start_at: "2020-01-01T00:00:00+00:00" }),
      ]);
      if (url.includes("/attempts/test/")) return json(404, { detail: "Not found" });
      return json(200, []);
    }));
    render(<MemoryRouter><ScheduledTestsPage /></MemoryRouter>);
    expect(await screen.findByText("Choose Papers")).toBeTruthy();
    expect(screen.queryByText("Start Test")).toBeNull();
    expect(screen.getByText("Questions depend on your papers")).toBeTruthy();
  });

  function renderList(tests: unknown[], attempt: (id: string) => Response = () => json(404, { detail: "Not found" })) {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/tests")) return json(200, tests);
      const m = url.match(/\/attempts\/test\/([^/]+)$/);
      if (m) return attempt(m[1]);
      return json(200, []);
    }));
    render(<MemoryRouter initialEntries={["/scheduled-tests"]}><Routes>
      <Route path="/scheduled-tests" element={<ScheduledTestsPage />} />
      <Route path="/tests/:testId" element={<p>details page</p>} />
      <Route path="/tests/:testId/attempt" element={<p>attempt page</p>} />
    </Routes></MemoryRouter>);
  }

  it("opens the details page for a Global Test whose papers need no choice, so the student reviews them before starting", async () => {
    renderList([test("t1", "NEET UG - 05", "Anchor", "B", { global_occurrence_id: "occ-1", total_questions: 4, start_at: "2020-01-01T00:00:00+00:00" })]);
    fireEvent.click(await screen.findByRole("button", { name: "View Papers" }));
    expect(await screen.findByText("details page")).toBeTruthy();
  });

  it("resumes a Global Test with an attempt in progress directly", async () => {
    renderList([test("t1", "NEET UG - 05", "Anchor", "B", { global_occurrence_id: "occ-1", total_questions: 4, start_at: "2020-01-01T00:00:00+00:00" })],
      () => json(200, { attempt_id: "a1", test_id: "t1", status: "IN_PROGRESS" }));
    fireEvent.click(await screen.findByRole("button", { name: "Resume Test" }));
    expect(await screen.findByText("attempt page")).toBeTruthy();
  });

  it("starts an ordinary test directly, as before", async () => {
    renderList([test("t2", "Weekly Physics", "Apex", "Thane", { start_at: "2020-01-01T00:00:00+00:00" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Start Test" }));
    expect(await screen.findByText("attempt page")).toBeTruthy();
  });
});
