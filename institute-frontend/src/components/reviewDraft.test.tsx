import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fail, stubApi } from "../test/stubApi";
import type { DraftView, ReviewDraft } from "../hooks/useDraftAutosave";
import type { Outcome, PaperPreview } from "./recognitionModel";
import { DraftReview } from "./reviewDraft";

afterEach(() => vi.unstubAllGlobals());
const SAVED = { timeout: 4000 };                                                       // autosave waits 1.5 s after the last change
const field = (original: string, outcome: Outcome, extra: Record<string, unknown> = {}) => ({ original, text: original, outcome, reasons: [], spans: [], ...extra });
const PREVIEW: PaperPreview = {
  review_token: "tok", engine_version: "2.4.0", domain: "mathematics",
  summary: { questions: 1, questions_needing_review: 1, fields: { CONVERTED: 0, PLAIN_TEXT: 4, NEEDS_REVIEW: 1, EXTRACTION_ERROR: 0 } },
  questions: [{ number: 1, row: 2, correct: "A", marks: "4", negative_marks: "1", topic: null, status: "NEEDS_REVIEW", fields: {
    question: field("Differentiate e13x.", "NEEDS_REVIEW", { reasons: ["FLATTENED_POWER"], spans: [{ original: "e13x", status: "flagged", latex: null,
      code: "FLATTENED_POWER", domain: "mathematics", start: 14, end: 18, reason: "r", explanation: "A small raised number may have been lost." }] }),
    option_a: field("1", "PLAIN_TEXT"), option_b: field("2", "PLAIN_TEXT"), option_c: field("3", "PLAIN_TEXT"), option_d: field("4", "PLAIN_TEXT") } }],
};
const draft = (extra: Partial<ReviewDraft> = {}): ReviewDraft => ({ draft_id: "d1", status: "OPEN", title: "Calculus", file_name: "paper.csv",
  subject_id: "math", revision: 1, decisions: {}, updated_at: "2026-11-13T10:00:00Z", result_paper_version_id: null, has_answer_key: false, ...extra });
const view = (d: ReviewDraft, extra: Partial<DraftView> = {}): DraftView & { preview: PaperPreview } => ({ draft: d, preview: PREVIEW, ...extra });
const URL_ = "/api/v1/review-drafts/d1";
const show = (d: ReviewDraft, extra: Record<string, unknown> = {}, more: Partial<DraftView> = {}) => {
  const onImported = vi.fn();
  const calls = stubApi({ "POST /api/v1/branches/b1/subjects/math/recognition-preview": { original: "", text: "", outcome: "PLAIN_TEXT", reasons: [], spans: [], suggestions: [] },
    ...extra });
  render(<DraftReview view={view(d, more)} target={{ branchId: "b1", subjectId: "math" }} onImported={onImported} onClose={vi.fn()} />);
  return { calls, onImported };
};
const keep = () => fireEvent.click(within(screen.getByTestId("rev-1-question")).getByLabelText(/Keep the text exactly as in the file/));

describe("A saved import review", () => {
  it("saves a decision shortly after it is made and says when", async () => {
    const { calls } = show(draft(), { [`PATCH ${URL_}`]: { revision: 2, updated_at: "2026-11-13T10:07:00Z" } });
    expect(document.querySelector(".save-status")!.textContent).toMatch(/All changes saved at/);
    keep();
    expect(document.querySelector(".save-status")!.textContent).toBe("Saving…");
    expect((screen.getByRole("button", { name: "Saving your decisions…" }) as HTMLButtonElement).disabled).toBe(true);   // never imports unsaved work
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1), SAVED);
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ base_revision: 1, changes: { "1:question": { action: "keep_original" } } });
    expect(await screen.findByRole("button", { name: "Import as a draft paper" })).toBeTruthy();
    expect(document.querySelector(".save-status")!.textContent).toMatch(/All changes saved at/);
  });

  it("reopens with the saved decisions (after a reload) and imports at the revision it has seen", async () => {
    const saved = draft({ revision: 4, decisions: { "1:question": { decision: { action: "keep_original" }, rev: 4, by: "s1", at: "2026-11-13T10:00:00Z" } } });
    const { calls, onImported } = show(saved, { [`POST ${URL_}/import`]: { paper_version_id: "v9", question_count: 1 } });
    expect((within(screen.getByTestId("rev-1-question")).getByLabelText(/Keep the text exactly/) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Import as a draft paper" }));
    await waitFor(() => expect(onImported).toHaveBeenCalledWith({ paper_version_id: "v9", question_count: 1 }));
    expect(calls.find((c) => c.path.endsWith("/import"))!.body).toEqual({ revision: 4 });
  });

  it("never overwrites someone else's change: both are shown, and the teacher's choice is saved deliberately", async () => {
    const theirs = { decision: { action: "edit", text: "Differentiate \\(e^{13x}\\)." }, rev: 2, by: "s2", at: "2026-11-13T10:06:00Z" };
    let round = 0;
    const { calls } = show(draft(), { [`PATCH ${URL_}`]: () => (++round === 1
      ? fail(409, { code: "DRAFT_CONFLICT", message: "changed", conflicts: { "1:question": theirs }, revision: 2 })
      : { revision: 3, updated_at: "2026-11-13T10:08:00Z" }) });
    keep();
    const alert = await screen.findByText(/was changed by someone else/, {}, SAVED);
    expect(alert.textContent).toContain("Question 1, Question");
    expect(alert.closest("[role=alert], .alert")!.querySelector(".katex")).toBeTruthy();             // their version, typeset
    expect((screen.getByRole("button", { name: "Resolve the changes above first" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Keep mine" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2), SAVED);
    expect(calls.filter((c) => c.method === "PATCH")[1].body).toEqual({ base_revision: 2, changes: { "1:question": { action: "keep_original" } } });
    expect(await screen.findByRole("button", { name: "Import as a draft paper" }, SAVED)).toBeTruthy();
  });

  it("can take the other person's version instead", async () => {
    const theirs = { decision: { action: "edit", text: "Differentiate \\(e^{13x}\\)." }, rev: 2, by: "s2", at: "2026-11-13T10:06:00Z" };
    const { calls } = show(draft(), { [`PATCH ${URL_}`]: fail(409, { code: "DRAFT_CONFLICT", message: "changed", conflicts: { "1:question": theirs }, revision: 2 }) });
    keep();
    fireEvent.click(await screen.findByRole("button", { name: "Use their version" }, SAVED));
    const correction = within(screen.getByTestId("rev-1-question")).getByLabelText(/Corrected text/) as HTMLTextAreaElement;
    expect(correction.value).toBe("Differentiate \\(e^{13x}\\).");
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);                         // nothing re-sent: theirs is already saved
  });

  it("says when a save failed and keeps the decision to send again", async () => {
    show(draft(), { [`PATCH ${URL_}`]: fail(503, { code: "UNAVAILABLE", message: "Service unavailable" }) });
    keep();
    expect(await screen.findByText(/Not saved - retrying \(Service unavailable\)/, {}, SAVED)).toBeTruthy();
    expect((within(screen.getByTestId("rev-1-question")).getByLabelText(/Keep the text exactly/) as HTMLInputElement).checked).toBe(true);
  });

  it("asks to confirm decisions made before the recognition was improved, and only then imports", async () => {
    const saved = draft({ revision: 2, decisions: { "1:question": { decision: { action: "keep_original" }, rev: 2, by: "s1", at: "2026-11-13T10:00:00Z" } } });
    const { calls } = show(saved, { [`PATCH ${URL_}`]: { revision: 3, updated_at: "2026-11-13T10:09:00Z" } },
      { engine_changed: true, stale_keys: ["1:question"] });
    expect(screen.getByText(/check and confirm each highlighted field \(1 left\)/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "1 field(s) still need a decision" }) as HTMLButtonElement).disabled).toBe(true);
    const field = screen.getByTestId("rev-1-question");
    expect((within(field).getByLabelText(/Keep the text exactly/) as HTMLInputElement).checked).toBe(true);   // kept, never dropped
    fireEvent.click(within(field).getByRole("button", { name: "Confirm this decision" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1), SAVED);
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ base_revision: 2, changes: { "1:question": { action: "keep_original" } } });
    expect(await screen.findByRole("button", { name: "Import as a draft paper" }, SAVED)).toBeTruthy();
  });
});
