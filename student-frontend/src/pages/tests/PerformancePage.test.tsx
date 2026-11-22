import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import PerformancePage from "../student/PerformancePage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderWith(attempt: Response) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.includes("/attempts/test/") ? attempt : json(200, {}))));
  render(<MemoryRouter initialEntries={["/tests/t1/performance"]}><Routes>
    <Route path="/tests/:testId/performance" element={<PerformancePage />} />
  </Routes></MemoryRouter>);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Performance page: a test not attempted yet", () => {
  it("shows 'not attempted yet' for the attempt service's 200 {has_attempt: false}", async () => {
    renderWith(json(200, { has_attempt: false }));
    expect(await screen.findByText("You have not attempted this test yet.")).toBeTruthy();
  });

  it("still accepts an older service's 404 the same way", async () => {
    renderWith(json(404, { detail: "No attempt found" }));
    expect(await screen.findByText("You have not attempted this test yet.")).toBeTruthy();
  });
});
