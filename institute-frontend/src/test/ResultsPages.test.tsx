import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "./renderApp";
import { fail, me, page, stubApi } from "./stubApi";
import { CorrectionsCard } from "../pages/ResultsPages";

const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]) };
const exam = { exam_id: "ex1", name: "Daily Physics", status: "PUBLISHED", start_at: "2026-10-25T04:00:00Z", end_at: "2026-10-25T08:00:00Z", assigned_students: 3,
  assignment_status: { COMPLETED: 2, ASSIGNED: 1 } };
const row = (rank: number, id: string, name: string, obtained: number) => ({ rank, student_id: id, name, obtained_marks: obtained, total_marks: 16, accuracy: 75,
  subjects: [{ subject: "Physics", obtained_marks: obtained, total_marks: 16 }] });
afterEach(() => vi.unstubAllGlobals());

describe("Results", () => {
  const user = { ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["result.read", "exam.read"]) };
  const summary = { window_closed: true, total_marks: "16.00", subject_view: false,
    summary: { assigned: 3, submitted: 2, not_submitted: 1, average: 10, highest: 12, lowest: 8 }, results: [row(1, "STU000002", "Zoya", 12)] };

  it("requests the results together with the exam, not after it", async () => {
    let release: (v: unknown) => void = () => {};
    const pending = new Promise((r) => { release = r; });
    const calls = stubApi({ ...user, "/api/v1/exams/ex1": () => pending, "/api/v1/exams/ex1/results": summary });
    renderApp("/results/ex1");
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/v1/exams/ex1/results")).toBe(true));
    release(exam);
    expect(await screen.findByText("Physics 12/16")).toBeTruthy();
  });

  it("starts the Super Admin's correction history with the exam, shows its failure only in its card, and never for other roles", async () => {
    let release: (v: unknown) => void = () => {};
    const pending = new Promise((r) => { release = r; });
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["result.read", "exam.read", "result.correct"]), "/api/v1/exams/ex1": () => pending,
      "/api/v1/exams/ex1/results": summary, "/api/v1/exams/ex1/integrity": { items: [] },
      "/api/v1/exams/ex1/result-corrections": fail(500, { code: "ERROR", message: "History unavailable." }) });
    const { unmount } = renderApp("/results/ex1");
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/v1/exams/ex1/result-corrections")).toBe(true));
    release(exam);
    expect(await screen.findByText("Physics 12/16")).toBeTruthy();                       // the results still show
    const card = (await screen.findByRole("heading", { name: "Result corrections" })).closest(".card") as HTMLElement;
    expect(within(card).getByText("History unavailable.")).toBeTruthy();
    expect(calls.filter((c) => c.path === "/api/v1/exams/ex1/result-corrections")).toHaveLength(1);   // the card reuses the page's request
    unmount();
    vi.unstubAllGlobals();
    const other = stubApi({ ...user, "/api/v1/exams/ex1": exam, "/api/v1/exams/ex1/results": summary });
    renderApp("/results/ex1");
    expect(await screen.findByText("Physics 12/16")).toBeTruthy();
    expect(other.some((c) => c.path.endsWith("/result-corrections"))).toBe(false);
  });

  it("shows only the exam's 404, not a correction error, when the exam does not exist", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["result.read", "exam.read", "result.correct"]),
      "/api/v1/exams/ex1": fail(404, { code: "NOT_FOUND", message: "Exam not found." }),
      "/api/v1/exams/ex1/results": fail(404, { code: "NOT_FOUND", message: "Exam not found." }),
      "/api/v1/exams/ex1/result-corrections": fail(404, { code: "NOT_FOUND", message: "Corrections: exam not found." }) });
    renderApp("/results/ex1");
    expect(await screen.findByText("Exam not found.")).toBeTruthy();
    expect(screen.queryByText(/Corrections: exam not found/)).toBeNull();
  });

  it("shows no results error for an unpublished exam (the results request answers 409)", async () => {
    stubApi({ ...user, "/api/v1/exams/ex1": { ...exam, status: "SCHEDULED" },
      "/api/v1/exams/ex1/results": fail(409, { code: "NOT_PUBLISHED", message: "Results exist only for published exams." }) });
    renderApp("/results/ex1");
    expect(await screen.findByText("Results exist only for published exams.")).toBeTruthy();   // the page's own notice, once
    expect(screen.getAllByText("Results exist only for published exams.")).toHaveLength(1);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows the exam's error when the exam cannot be read, whatever the results request answered", async () => {
    stubApi({ ...user, "/api/v1/exams/ex1": fail(404, { code: "NOT_FOUND", message: "Exam not found." }),
      "/api/v1/exams/ex1/results": fail(403, { code: "FORBIDDEN", message: "You do not have permission for this action." }) });
    renderApp("/results/ex1");
    expect(await screen.findByText("Exam not found.")).toBeTruthy();
    expect(screen.queryByText(/do not have permission/)).toBeNull();
  });

  it("shows a results failure of a published exam on its own, keeping the exam header", async () => {
    stubApi({ ...user, "/api/v1/exams/ex1": exam,
      "/api/v1/exams/ex1/results": fail(403, { code: "FORBIDDEN", message: "You do not have permission for this action." }) });
    renderApp("/results/ex1");
    expect(await screen.findByText("You do not have permission for this action.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Daily Physics" })).toBeTruthy();
  });

  it("lists published exams of every provider and branch in scope, without a Working In selector, and searches them on the server", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", ["result.read", "exam.read"]),
      "/api/v1/exams": page([{ ...exam, provider_name: "Alpha", branch_name: "Main" }, { ...exam, exam_id: "ex2", name: "National Mock", unified_global: true }]) });
    renderApp("/results");
    expect(await screen.findByText("Daily Physics")).toBeTruthy();
    expect(screen.getByText(/Submitted attempts and evaluated performance/)).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Working in" })).toBeNull();                     // the selector is gone
    expect(screen.queryByText("Working in")).toBeNull();
    expect(screen.queryByLabelText("Provider")).toBeNull();
    expect(screen.queryByLabelText("Branch")).toBeNull();
    expect(calls.some((c) => c.path.startsWith("/api/v1/branches/"))).toBe(false);              // no branch has to be chosen first
    expect(calls.find((c) => c.path === "/api/v1/exams")?.search.get("status")).toBe("PUBLISHED");   // only exams with results
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Exam", "Provider / branch", "Available", "Assigned", "Completed", ""]);
    expect(within(table).getByText("Alpha · Main")).toBeTruthy();
    expect(within(table).getByText("Global Test")).toBeTruthy();
    expect(within(table).getAllByRole("link", { name: "View results" })[0].getAttribute("href")).toBe("/results/ex1");
    expect(within(table).getAllByText("2").length).toBeGreaterThan(0);                          // completed count kept
    fireEvent.change(screen.getByLabelText("Search exams"), { target: { value: "phys" } });
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/v1/exams" && c.search.get("q") === "phys")).toBe(true));
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

const globalRow = { rank: 1, student_id: "STU000001", name: "Asha K", obtained_marks: 7, total_marks: 16, accuracy: 50,
  subjects: [{ subject: "Physics", obtained_marks: 4, total_marks: 8, paper_id: "pa" }, { subject: "Chemistry", obtained_marks: 3, total_marks: 8, paper_id: "pc" }] };

describe("Result corrections of a Global Test", () => {
  it("lets a contributor correct only the answer key (their own papers), without marks or history", async () => {
    const calls = stubApi({ "POST /api/v1/exams/e1/answer-key-corrections": { status: "APPLIED" } });
    render(<CorrectionsCard examId="e1" platform={false} globalTest rows={[]} onChanged={() => {}} />);
    expect(screen.getByText("You can correct the answer key of your own papers only.")).toBeTruthy();
    expect(screen.queryByText("Correct one student's marks")).toBeNull();
    expect(screen.queryByText("History")).toBeNull();
    fireEvent.change(screen.getByLabelText("Question number"), { target: { value: "3" } });
    fireEvent.change(screen.getAllByLabelText("Reason (required)")[0], { target: { value: "Key printed wrongly" } });
    fireEvent.click(screen.getByRole("button", { name: "Correct key and re-evaluate" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.some((c) => c.path.endsWith("/result-corrections"))).toBe(false);   // the platform-only history is never asked for
  });

  it("asks the platform for the marks per subject of the student's attempt and sends their total", async () => {
    const calls = stubApi({ "/api/v1/exams/e1/result-corrections": [], "POST /api/v1/exams/e1/results/STU000001/adjustments": { status: "APPLIED" } });
    render(<CorrectionsCard examId="e1" platform globalTest rows={[globalRow]} onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText("Student ID"), { target: { value: "stu000001" } });
    fireEvent.change(screen.getByLabelText("Physics marks (out of 8, now 4)"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("Chemistry marks (out of 8, now 3)"), { target: { value: "3" } });
    expect(screen.getByText("New total: 9")).toBeTruthy();
    fireEvent.change(screen.getAllByLabelText("Reason (required)")[0], { target: { value: "Re-checked by hand" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply correction" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ obtained_marks: 9, reason: "Re-checked by hand",
      subjects: [{ subject: "Physics", obtained_marks: 6 }, { subject: "Chemistry", obtained_marks: 3 }] });
  });

  it("keeps the single marks field for every other exam", () => {
    stubApi({ "/api/v1/exams/e1/result-corrections": [] });
    render(<CorrectionsCard examId="e1" platform globalTest={false} rows={[globalRow]} onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText("Student ID"), { target: { value: "STU000001" } });
    expect(screen.getByLabelText("Correct marks")).toBeTruthy();
    expect(screen.queryByLabelText(/Physics marks/)).toBeNull();
  });
});
