import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
});
