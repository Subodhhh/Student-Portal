import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RankingPage from "./RankingPage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const row = (rank: number, id: string, name: string, obtained: number, max: number, percentage: number) =>
  ({ rank, student_id: id, student_name: name, obtained_marks: obtained, max_marks: max, percentage });

const GLOBAL = {
  test_id: "t1", my_student_id: "S2", occurrence_id: "occ-1", total_students: 3, my_rank: 3, my_score: 7, my_max_marks: 16, my_percentage: 43.75,
  leaderboard: [row(1, "S1", "Asha", 16, 16, 100), row(1, "S3", "Chitra", 8, 8, 100), row(3, "S2", "Bala", 7, 16, 43.75)],
  subjects: [
    { subject: "Biology", total_students: 1, my_rank: null, my_score: null, my_max_marks: null, my_percentage: null, leaderboard: [row(1, "S3", "Chitra", 8, 8, 100)] },
    { subject: "Physics", total_students: 2, my_rank: 1, my_score: 8, my_max_marks: 8, my_percentage: 100,
      leaderboard: [row(1, "S1", "Asha", 8, 8, 100), row(1, "S2", "Bala", 8, 8, 100)] },
    { subject: "Chemistry", total_students: 0, my_rank: null, my_score: null, my_max_marks: null, my_percentage: null, leaderboard: [] },
  ],
};

function renderWith(results: unknown[], ranking: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.endsWith("/results") ? json(200, results) : json(200, ranking))));
  render(<RankingPage />);
}

afterEach(() => vi.unstubAllGlobals());

describe("Ranking", () => {
  it("shows a Global Test's overall ranking and one tab per configured subject, in order", async () => {
    renderWith([{ test_id: "t1", test_name: "NEET UG - 02(testing)", total_marks: 16, obtained_marks: 7 }], GLOBAL);
    expect(await screen.findByText("Overall Ranking")).toBeTruthy();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overall", "Biology", "Physics", "Chemistry"]);
    expect(screen.getByText("7 / 16 (43.75%)")).toBeTruthy();
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows).toEqual(["#1Asha1616100%", "#1Chitra88100%", "#3Bala71643.75%"]);       // equal percentages share a rank

    fireEvent.click(screen.getByRole("tab", { name: "Biology" }));
    expect(screen.getByText("No paper")).toBeTruthy();                                         // no Biology paper: not ranked, not 0%
    expect(screen.getByText("Not ranked")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Chemistry" }));
    expect(screen.getByText(/Nobody who submitted this test received a Chemistry paper/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Physics" }));
    expect(screen.getByText("#1", { selector: "strong" })).toBeTruthy();
  });

  it("keeps the Daily/Weekly ranking as it was", async () => {
    renderWith([{ test_id: "d1", test_name: "Daily Physics", total_marks: 8, obtained_marks: 3 }],
      { test_id: "d1", my_student_id: "S1", my_rank: 1, total_students: 1, my_score: 3, leaderboard: [{ rank: 1, student_id: "S1", student_name: "Asha", obtained_marks: 3 }] });
    expect(await screen.findByText("Test Ranking")).toBeTruthy();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(screen.getByText("3 / 8")).toBeTruthy();
  });
});
