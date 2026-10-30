import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import TestsPage from "./TestsPage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const test = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  test_id: id, name, description: null, duration_minutes: 180, total_questions: 4, total_marks: 16, status: "ACTIVE",
  start_at: "2020-01-01T00:00:00+00:00", end_at: "2099-01-01T00:00:00+00:00",
  provider: { provider_id: `p-${id}`, name: "Apex" }, branch: { branch_id: `b-${id}`, name: "Thane", city: null, state: null }, ...extra });

function renderTests(tests: unknown[], attempt: (id: string) => Response = () => json(404, { detail: "Not found" })) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/enrollments")) return json(200, []);
    if (url.endsWith("/tests")) return json(200, tests);
    const m = url.match(/\/attempts\/test\/([^/]+)$/);
    if (m) return attempt(m[1]);
    return json(200, []);
  }));
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign });
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  render(<TestsPage />);
  return { assign, open };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Available tests", () => {
  it("opens the details page for a Global Test whose papers need no choice, never the exam", async () => {
    const { assign, open } = renderTests([test("t1", "NEET UG - 05", { global_occurrence_id: "occ-1" })]);
    fireEvent.click(await screen.findByRole("button", { name: "View Papers" }));
    expect(assign).toHaveBeenCalledWith("/tests/t1");
    expect(open).not.toHaveBeenCalled();
  });

  it("resumes a Global Test with an attempt in progress directly", async () => {
    const { assign, open } = renderTests([test("t1", "NEET UG - 05", { global_occurrence_id: "occ-1" })],
      () => json(200, { attempt_id: "a1", test_id: "t1", status: "IN_PROGRESS" }));
    fireEvent.click(await screen.findByRole("button", { name: "Resume Test" }));
    expect(open).toHaveBeenCalledWith("/tests/t1/attempt", "_blank");
    expect(assign).not.toHaveBeenCalled();
  });

  it("starts an ordinary test directly, as before", async () => {
    const { assign, open } = renderTests([test("t2", "Weekly Physics")]);
    fireEvent.click(await screen.findByRole("button", { name: "Start Test" }));
    expect(open).toHaveBeenCalledWith("/tests/t2/attempt", "_blank");
    expect(assign).not.toHaveBeenCalled();
  });
});
