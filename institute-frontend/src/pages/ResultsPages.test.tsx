import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]) };
const exam = { exam_id: "ex1", name: "Daily Physics", status: "PUBLISHED", start_at: "2026-10-25T04:00:00Z", end_at: "2026-10-25T08:00:00Z", assigned_students: 3,
  assignment_status: { COMPLETED: 2, ASSIGNED: 1 } };
const row = (rank: number, id: string, name: string, obtained: number) => ({ rank, student_id: id, name, obtained_marks: obtained, total_marks: 16, accuracy: 75,
  subjects: [{ subject: "Physics", obtained_marks: obtained, total_marks: 16 }] });
afterEach(() => vi.unstubAllGlobals());

describe("Results", () => {
  it("lists published exams with a clear purpose and searches them on the server", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["result.read", "exam.read"]), "/api/v1/branches/b1/exams": page([exam]) });
    renderApp("/results");
    expect(await screen.findByText("Daily Physics")).toBeTruthy();
    expect(screen.getByText(/Submitted attempts and evaluated performance/)).toBeTruthy();
    expect(calls.find((c) => c.path === "/api/v1/branches/b1/exams")?.search.get("status")).toBe("PUBLISHED");   // only exams with results
    fireEvent.change(screen.getByLabelText("Search exams"), { target: { value: "phys" } });
    await vi.waitFor(() => expect(calls.some((c) => c.search.get("q") === "phys")).toBe(true));
  });

  it("shows one exam's results with the subject breakdown, student search and sorting", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["result.read", "exam.read"]), "/api/v1/exams/ex1": exam,
      "/api/v1/exams/ex1/results": { window_closed: true, total_marks: "16.00", subject_view: false,
        summary: { assigned: 3, submitted: 2, not_submitted: 1, average: 10, highest: 12, lowest: 8 },
        results: [row(1, "STU000002", "Zoya", 12), row(2, "STU000001", "Asha", 8)] } });
    renderApp("/results/ex1");
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Physics 12/16")).toBeTruthy();
    expect(screen.getByText("2/3")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "name" } });
    expect(within(table).getAllByRole("row").slice(1).map((r) => r.textContent?.includes("Asha"))).toEqual([true, false]);
    fireEvent.change(screen.getByLabelText("Search students"), { target: { value: "zoya" } });
    expect(within(table).getAllByRole("row")).toHaveLength(2);                          // header + Zoya
    expect(screen.getByRole("link", { name: "Exam" }).getAttribute("href")).toBe("/exams/ex1");
  });
});
