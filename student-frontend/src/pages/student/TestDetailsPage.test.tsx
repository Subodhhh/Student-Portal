import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  describe("Global Test with paper choice paper choice", () => {
    const paper = (id: string, label: string, n: number) => ({ paper_id: id, label, provider_name: `${label} Academy`, question_count: n, total_marks: 4 * n });
    const choiceSections = [
      { position: 1, subject: "Physics", status: "SELECTABLE", message: null, question_count: 0, papers: [paper("pa", "Physics A", 45), paper("pb", "Physics B", 40)] },
      { position: 2, subject: "Chemistry", status: "PAPER", message: null, question_count: 45, papers: [paper("pc", "Chemistry C", 45)] },
      { position: 3, subject: "Biology", status: "NOT_ENROLLED", message: "You are not enrolled in Biology.", question_count: 0, papers: [] },
    ];
    const view = (physics: string | null, extra: Record<string, unknown> = {}) => ({
      test_id: "t5", locked: false, complete: physics !== null, attempt_id: null,
      subjects: [
        { ...choiceSections[0], selected_paper_id: physics },
        { ...choiceSections[1], selected_paper_id: "pc" },
        { ...choiceSections[2], selected_paper_id: null },
      ], ...extra });

    function renderV2(initial: unknown, onPut: (body: { subject: string; paper_id: string }) => Response) {
      const calls: { url: string; method: string; body: unknown }[] = [];
      vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : null;
        calls.push({ url, method: init?.method ?? "GET", body });
        if (url.endsWith("/tests/t5/details")) {
          return json(200, test("t5", "Anchor", "B", { global_occurrence_id: "occ-1", start_at: "2020-01-01T00:00:00+00:00",
                                                       total_questions: null, total_marks: null, sections: choiceSections }));
        }
        if (url.endsWith("/attempts/tests/t5/papers")) return json(200, initial);
        if (url.endsWith("/attempts/tests/t5/selection")) return onPut(body);
        return json(404, { detail: "Not found" });
      }));
      render(<MemoryRouter initialEntries={["/tests/t5"]}><Routes><Route path="/tests/:testId" element={<TestDetailsPage />} /></Routes></MemoryRouter>);
      return calls;
    }

    it("shows every subject's status and papers, keeps Start disabled until the choice is made, and saves the choice", async () => {
      const calls = renderV2(view(null), (b) => json(200, view(b.paper_id)));
      expect(await screen.findByText("Choose a Physics paper")).toBeTruthy();
      expect(screen.getByText(/Chemistry C · Chemistry C Academy · 45 question\(s\)/)).toBeTruthy();   // one paper: included, no choice
      expect(screen.getByText("You are not enrolled in Biology.")).toBeTruthy();                       // the missing-subject reason
      expect(screen.getAllByText("Depends on your papers")).toHaveLength(2);                          // questions and marks
      const start = screen.getByRole("button", { name: "Take Test" }) as HTMLButtonElement;
      expect(start.disabled).toBe(true);
      fireEvent.click(screen.getByLabelText(/Physics B/));
      await waitFor(() => expect((screen.getByRole("button", { name: "Take Test" }) as HTMLButtonElement).disabled).toBe(false));
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ subject: "Physics", paper_id: "pb" });
      fireEvent.click(screen.getByLabelText(/Physics A/));                                            // changed again before starting
      await waitFor(() => expect(calls.filter((c) => c.method === "PUT").length).toBe(2));
      expect((screen.getByLabelText(/Physics A/) as HTMLInputElement).checked).toBe(true);
    });

    it("restores a saved choice after a refresh (the service is the source)", async () => {
      renderV2(view("pb"), () => json(500, {}));
      expect(((await screen.findByLabelText(/Physics B/)) as HTMLInputElement).checked).toBe(true);
      expect((screen.getByRole("button", { name: "Take Test" }) as HTMLButtonElement).disabled).toBe(false);
    });

    it("once started shows only the attempt's papers, without any choice", async () => {
      renderV2(view("pb", { locked: true, attempt_id: "a1" }), () => json(500, {}));
      expect(await screen.findByText("The test has started. These are your papers.")).toBeTruthy();
      expect(screen.queryByText("Choose a Physics paper")).toBeNull();
      expect(screen.getByText(/Physics B · Physics B Academy/)).toBeTruthy();
      expect(screen.queryByText(/Physics A/)).toBeNull();
    });

    it("explains a refused choice (the test was started elsewhere) and shows the locked papers", async () => {
      let locked = false;
      renderV2(view(null), () => { locked = true; return json(409, { detail: { code: "SELECTION_LOCKED" } }); });
      const fetchMock = vi.mocked(globalThis.fetch);
      const original = fetchMock.getMockImplementation()!;
      fetchMock.mockImplementation(async (url, init) =>
        String(url).endsWith("/attempts/tests/t5/papers") && locked ? json(200, view("pa", { locked: true })) : original(url, init));
      fireEvent.click(await screen.findByLabelText(/Physics B/));
      expect(await screen.findByText("Your papers are fixed because the test has started.")).toBeTruthy();
      expect(await screen.findByText("The test has started. These are your papers.")).toBeTruthy();
    });
  });
});
