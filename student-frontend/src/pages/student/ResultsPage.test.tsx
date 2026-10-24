import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ResultsPage from "./ResultsPage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const result = (id: string, name: string, providerId: string, providerName: string, extra: Record<string, unknown> = {}) => ({
  result_id: id, attempt_id: `a-${id}`, test_id: `t-${id}`, student_id: "STU000001", test_name: name, provider_id: providerId,
  provider_name: providerName, branch_id: `b-${id}`, branch_name: "", city: null, state: null, total_marks: 36, obtained_marks: 23,
  created_at: "2026-10-24T17:40:00Z", ...extra });

afterEach(() => vi.unstubAllGlobals());

describe("Results", () => {
  it("lists a unified Global Test result as the Global Test under every institute, never as one institute's own", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/results")) return json(200, [
        result("r1", "NEET UG - 02 (2026-11-07)", "p-north", "Global Test", { global_occurrence_id: "occ-1" }),   // anchor: North
        result("r2", "Weekly Physics", "p-north", "North Academy"),
      ]);
      if (url.endsWith("/enrollments")) return json(200, [
        { provider: { provider_id: "p-north", name: "North Academy" }, status: "ACTIVE" },
        { provider: { provider_id: "p-ravi", name: "Ravi Tutorials" }, status: "ACTIVE" }]);
      return json(200, []);
    }));
    render(<MemoryRouter><ResultsPage /></MemoryRouter>);
    expect(await screen.findByText("NEET UG - 02 (2026-11-07)")).toBeTruthy();
    expect(screen.getByText("Global Test")).toBeTruthy();
    const filter = screen.getByRole("combobox");
    fireEvent.change(filter, { target: { value: "p-ravi" } });                   // the tutor supplied part of it, not the anchor
    expect(screen.getByText("NEET UG - 02 (2026-11-07)")).toBeTruthy();
    expect(screen.queryByText("Weekly Physics")).toBeNull();                      // North's own test stays under North only
    fireEvent.change(filter, { target: { value: "p-north" } });
    expect(screen.getByText("NEET UG - 02 (2026-11-07)")).toBeTruthy();
    expect(screen.getByText("Weekly Physics")).toBeTruthy();
  });
});
