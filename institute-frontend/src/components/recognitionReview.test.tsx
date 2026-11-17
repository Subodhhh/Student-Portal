import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RecognitionReview } from "./recognitionReview";
import type { PaperPreview } from "./recognitionModel";

const EXPLAIN = "A small raised number or letter may have been lost when this was copied (e13x could be e¹³x or e¹³ˣ).";
const preview: PaperPreview = {
  review_token: "t", engine_version: "2.4.0", domain: "mathematics",
  summary: { questions: 1, questions_needing_review: 1, fields: { CONVERTED: 0, PLAIN_TEXT: 0, NEEDS_REVIEW: 1, EXTRACTION_ERROR: 0 } },
  questions: [{ number: 1, row: 2, correct: "A", marks: "4", negative_marks: "1", topic: null, status: "NEEDS_REVIEW", fields: {
    question: { original: "Differentiate e13x.", text: "Differentiate e13x.", outcome: "NEEDS_REVIEW", reasons: ["FLATTENED_POWER"],
      spans: [{ original: "e13x", status: "flagged", latex: null, code: "FLATTENED_POWER", domain: "mathematics", start: 14, end: 18,
        reason: "'e' written against a letter or a number is either e to that power or e times it.", explanation: EXPLAIN }] },
  } }],
};

describe("Import review wording", () => {
  it("tells the teacher what is unclear in plain words and never shows recognition codes, LaTeX or the engine version", () => {
    const { container } = render(<RecognitionReview preview={preview} onImport={vi.fn()} onCancel={vi.fn()} source="paper.xlsx" />);
    const field = screen.getByTestId("rev-1-question");
    const reasons = field.querySelector(".recognition-reasons") as HTMLElement;
    expect(within(reasons).getByText(EXPLAIN, { exact: false })).toBeTruthy();
    expect(container.textContent).not.toContain("FLATTENED_POWER");
    expect(container.textContent).not.toContain("2.4.0");
    expect(container.querySelector("details.recognition-diagnostics")).toBeNull();
  });

  it("falls back to the stored reason for records made before explanations existed", () => {
    const old = structuredClone(preview);
    const span = old.questions[0].fields.question!.spans[0];
    delete span.explanation; delete span.start; delete span.end;
    render(<RecognitionReview preview={old} onImport={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(/either e to that power or e times it/)).toBeTruthy();
  });
});


const READINGS = ["\\(\\sin^{3} x\\cos^{2} x\\)", "\\(\\sin 3x\\cos 2x\\)", "\\(\\sin^{3} x\\cos 2x\\)", "\\(\\sin 3x\\cos^{2} x\\)"];

function withReadings(texts: string[], complete = true, combinations = texts.length): PaperPreview {
  const p = structuredClone(preview);
  const field = p.questions[0].fields.question!;
  field.original = "sin3 x cos2x";
  field.suggestions = texts.map((text, k) => ({ expression: "sin3 x cos2x", label: `reading ${k + 1}`, text }));
  field.candidate_sets = [{ expression: "sin3 x cos2x", shown: texts.length, combinations, checked: Math.min(combinations, 81),
    rejected: 0, complete, limit: 81 }];
  return p;
}

describe("Every reading of an ambiguous expression", () => {
  it("shows every reading typeset, and says the list is complete", () => {
    render(<RecognitionReview preview={withReadings(READINGS)} onImport={vi.fn()} onCancel={vi.fn()} />);
    const group = screen.getByRole("group", { name: /How should sin3 x cos2x be read/ });
    expect(within(group).getAllByRole("button", { name: /Use reading/ })).toHaveLength(4);
    expect(group.textContent).toContain("These are all 4 ways it can be read.");
    expect(group.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(4);
  });

  it("pages long lists instead of cutting them, and discloses a list the engine could not complete", () => {
    const many = Array.from({ length: 9 }, (_, k) => `\\(x_{${k}}\\)`);
    render(<RecognitionReview preview={withReadings(many, false, 243)} onImport={vi.fn()} onCancel={vi.fn()} />);
    const group = screen.getByRole("group", { name: /How should/ });
    expect(within(group).getAllByRole("button", { name: /Use reading/ })).toHaveLength(6);
    fireEvent.click(within(group).getByRole("button", { name: "Show all 9 readings" }));
    expect(within(group).getAllByRole("button", { name: /Use reading/ })).toHaveLength(9);
    expect(group.textContent).toContain("can be read in 243 ways");
  });

  it("picking a reading fills the decision with exactly that reading", () => {
    const onImport = vi.fn();
    render(<RecognitionReview preview={withReadings(READINGS)} onImport={onImport} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Use reading 1/ }));
    fireEvent.click(screen.getByRole("button", { name: "Import as a draft paper" }));
    expect(onImport).toHaveBeenCalledWith({ "1:question": { action: "edit", text: READINGS[0] } });
  });
});

describe("Pictures in the preview", () => {
  it("shows the question picture and picture options where students will see them", async () => {
    const p = structuredClone(preview);
    const q = p.questions[0];
    q.status = "READY";
    q.fields = { question: { original: "Which organelle is shown?", text: "Which organelle is shown?", outcome: "PLAIN_TEXT", reasons: [], spans: [] } };
    q.images = { question: { asset_id: "a1", name: "cell.png", width: 40, height: 30, mime: "image/png" },
                 A: { asset_id: "a2", name: "opt.png", width: 40, height: 30, mime: "image/png" } };
    q.chapter = "Cell"; q.sub_topic = "Plastids";
    p.summary.questions_needing_review = 0;                 // nothing to decide: the question is shown by default
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<RecognitionReview preview={p} onImport={vi.fn()} onCancel={vi.fn()} />);
    expect((screen.getByAltText("Question picture (cell.png)") as HTMLImageElement).src).toMatch(/\/api\/v1\/question-images\/a1$/);
    expect(within(screen.getByTestId("pic-1-A")).getByText("Option A (correct answer)")).toBeTruthy();
    expect(screen.getByText("Cell › Plastids")).toBeTruthy();
    const optionA = screen.getByAltText("Option A picture (opt.png)") as HTMLImageElement;
    expect(optionA.src).toMatch(/\/api\/v1\/question-images\/a2$/);
    // the pictures are shown exactly as uploaded: nothing is fetched to read them, and nothing is said about their contents
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toMatch(/in this picture|Reading this picture/);
    vi.unstubAllGlobals();
  });
});
