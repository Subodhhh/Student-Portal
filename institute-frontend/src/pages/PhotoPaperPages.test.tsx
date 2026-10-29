import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi, type Call } from "../test/stubApi";

const q = (n: number, over: Record<string, unknown> = {}) => ({ number: n, question: `Question text ${n}`, option_a: "a", option_b: "b", option_c: "c", option_d: "d",
  correct: "A", marks: "4", negative_marks: "1", explanation: "", topic: "", confidence: 0.99, page: 1, issues: [], needs_review: false, reviewed: false,
  has_diagram: false, ...over });
const draft = (over: Record<string, unknown> = {}) => ({ draft_id: "d1", title: "Physics photo paper", status: "DRAFT", provider_id: "p1", branch_id: "b1",
  subject_id: "phy", created_at: "", extracted_at: null, accepted_paper_version_id: null, files: [], questions: [], issues: [], corrections: 0, needs_review: [], ...over });
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/auth/me": me("TEACHER", ["paper.read", "paper.upload", "paper.publish"]),
  "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]), "/api/v1/providers/p1/branches": page([{ branch_id: "b1", name: "Pune" }]),
  "/api/v1/providers/p1/subjects": [{ subject_id: "phy", name: "Physics" }] };
const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.path === path);

/** Minimal XMLHttpRequest stand-in for the progress uploads: records each request and answers with the given JSON. */
function stubXhr(respond: (form: FormData) => [number, unknown]) {
  const sent: FormData[] = [];
  class FakeXhr {
    status = 0; responseText = ""; withCredentials = false;
    upload = { onprogress: null as ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
    onload: (() => void) | null = null; onerror: (() => void) | null = null;
    open() {} setRequestHeader() {}
    send(form: FormData) {
      sent.push(form);
      setTimeout(() => {
        this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
        const [status, body] = respond(form);
        this.status = status; this.responseText = JSON.stringify(body);
        this.onload?.();
      }, 0);
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return sent;
}
afterEach(() => vi.unstubAllGlobals());

describe("Photo papers", () => {
  it("starts a photo paper from Question Bank & Papers", async () => {
    const calls = stubApi({ ...common, "/api/v1/photo-drafts": { items: [], total: 0 }, "POST /api/v1/branches/b1/subjects/phy/photo-drafts": draft(),
      "/api/v1/photo-drafts/d1": draft() });
    renderApp("/papers/photo");
    await screen.findByRole("option", { name: "Physics" }, { timeout: 5000 });          // the subject list has loaded
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Physics photo paper" } });
    const start = screen.getByRole("button", { name: "Start" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));                          // subjects loaded and chosen
    fireEvent.click(start);
    await waitFor(() => expect(posts(calls, "/api/v1/branches/b1/subjects/phy/photo-drafts")).toHaveLength(1), { timeout: 5000 });
    expect(await screen.findByRole("heading", { level: 2, name: "Physics photo paper" }, { timeout: 5000 })).toBeTruthy();
    expect(posts(calls, "/api/v1/branches/b1/subjects/phy/photo-drafts")[0].headers["Idempotency-Key"]).toBeTruthy();
  }, 15000);

  it("uploads several page photos in order with progress, then reads the questions", async () => {
    let files: unknown[] = [];
    const sent = stubXhr((form) => { files = [...files, { draft_file_id: `f${files.length + 1}`, kind: form.get("kind"), position: Number(form.get("position")), file_id: "x", lines: 12, ocr_error: null }];
      return [201, files[files.length - 1]]; });
    const calls = stubApi({ ...common, "/api/v1/photo-drafts/d1": () => draft({ files }), "POST /api/v1/photo-drafts/d1/extract": draft() });
    renderApp("/papers/photo/d1");
    const input = await screen.findByLabelText("Add page photos");
    expect(input.getAttribute("capture")).toBe("environment");
    fireEvent.change(input, { target: { files: [new File(["p1"], "IMG_1.jpg", { type: "image/jpeg" }), new File(["p2"], "IMG_2.jpg", { type: "image/jpeg" })] } });
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent.map((f) => [f.get("kind"), f.get("position")])).toEqual([["PAGE", "1"], ["PAGE", "2"]]);
    expect(await screen.findByText(/Page 2 · 12 line\(s\) read/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Read questions from the photos" }));
    await waitFor(() => expect(posts(calls, "/api/v1/photo-drafts/d1/extract")).toHaveLength(1));
  });

  it("highlights uncertain questions, saves corrections and imports only after an explicit confirmation", async () => {
    let accepted = false;
    const calls = stubApi({ ...common,
      "/api/v1/photo-drafts/d1": () => (accepted ? draft({ status: "ACCEPTED", accepted_paper_version_id: "pv9", extracted_at: "2030-01-01", questions: [q(1), q(2)] })
        : draft({ extracted_at: "2030-01-01", files: [{ draft_file_id: "f1", kind: "PAGE", position: 1, file_id: "x", lines: 9, ocr_error: null }],
          questions: [q(1), q(2, { confidence: 0.61, needs_review: true, issues: ["Option D could not be read."], option_d: "" })],
          issues: ["1 answer-key line(s) were read with low confidence: check every answer in the preview."] })),
      "PUT /api/v1/photo-drafts/d1/questions": (_u: URL, init: RequestInit) => draft({ extracted_at: "2030-01-01", questions: JSON.parse(String(init.body)).questions, corrections: 2 }),
      "POST /api/v1/photo-drafts/d1/accept": (_u: URL, init: RequestInit) => {
        const body = JSON.parse(String(init.body));
        if (!body.confirm) return fail(422, { code: "CONFIRMATION_REQUIRED", message: "Confirm." });
        accepted = true;
        return { ...draft({ status: "ACCEPTED" }), paper_version_id: "pv9" };
      } });
    renderApp("/papers/photo/d1");
    const flagged = await screen.findByRole("region", { name: "Question 2" });
    expect(flagged.className).toContain("needs-review");
    expect(within(flagged).getByText("Option D could not be read.")).toBeTruthy();
    expect(within(flagged).getByText("read with 61% confidence")).toBeTruthy();
    expect(screen.getByText(/answer-key line\(s\) were read with low confidence/)).toBeTruthy();
    expect(screen.getByText("1 highlighted question(s) still to check.")).toBeTruthy();
    fireEvent.change(within(flagged).getByLabelText("Question 2 option D"), { target: { value: "1500 m/s" } });
    fireEvent.click(within(flagged).getByLabelText("I checked question 2 against the photo"));
    fireEvent.click(screen.getByRole("button", { name: "Import paper" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/Imported as a draft paper version/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "open the paper" }).getAttribute("href")).toBe("/papers/pv9");
    const saved = calls.find((c) => c.method === "PUT")!.body as { questions: { number: number; option_d: string; reviewed: boolean }[] };
    expect(saved.questions[1]).toMatchObject({ number: 2, option_d: "1500 m/s", reviewed: true });
    expect(posts(calls, "/api/v1/photo-drafts/d1/accept")[0].body).toEqual({ confirm: true });
  });

  it("shows exactly which questions block an import, and imports nothing", async () => {
    stubApi({ ...common, "/api/v1/photo-drafts/d1": draft({ extracted_at: "2030-01-01", questions: [q(1), q(2, { correct: "" })] }),
      "POST /api/v1/photo-drafts/d1/accept": fail(422, { code: "EXTRACTION_FAILED", message: "Nothing was imported: 1 question(s) still need correcting.",
        errors: [{ row: 2, field: "correct", message: "Correct answer is missing." }], questions: [2] }) });
    renderApp("/papers/photo/d1");
    fireEvent.click(await screen.findByRole("button", { name: "Import paper" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Import" }));
    expect(await screen.findByText("Question 2: Correct answer is missing.")).toBeTruthy();
    expect(screen.getByText("Nothing was imported.")).toBeTruthy();
    expect(screen.queryByText(/Imported as a draft paper version/)).toBeNull();
  });

  it("discards a draft after confirmation", async () => {
    const calls = stubApi({ ...common, "/api/v1/photo-drafts/d1": draft(), "POST /api/v1/photo-drafts/d1/discard": draft({ status: "DISCARDED" }),
      "/api/v1/photo-drafts": { items: [], total: 0 } });
    renderApp("/papers/photo/d1");
    fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Discard" }));
    expect(await screen.findByText("No photo papers in progress")).toBeTruthy();
    expect(posts(calls, "/api/v1/photo-drafts/d1/discard")).toHaveLength(1);
  });

  it("keeps the reason for a refused import visible after the corrections it saved rebuild the preview", async () => {
    let corrections = 0;
    stubApi({ ...common,
      "/api/v1/photo-drafts/d1": () => draft({ extracted_at: "2030-01-01", corrections, questions: [q(1, { needs_review: true, confidence: 0.9 }), q(2)] }),
      "PUT /api/v1/photo-drafts/d1/questions": () => { corrections += 1; return draft(); },
      "POST /api/v1/photo-drafts/d1/accept": fail(422, { code: "DIAGRAM_NOT_SUPPORTED", message: "Questions that need their diagram cannot be imported yet.", questions: [1] }) });
    renderApp("/papers/photo/d1");
    fireEvent.click(await screen.findByLabelText("Question 1 needs its diagram"));
    fireEvent.click(screen.getByRole("button", { name: "Import paper" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/Questions that need their diagram cannot be imported yet\./)).toBeTruthy();
    await waitFor(() => expect(corrections).toBe(1));
    expect(await screen.findByText(/Questions: 1\./)).toBeTruthy();
    expect(screen.getByText("Nothing was imported.")).toBeTruthy();
  });

  it("fills marks only where the photo had none, with the values the uploader enters", async () => {
    const calls = stubApi({ ...common, "/api/v1/photo-drafts/d1": draft({ extracted_at: "2030-01-01", questions: [q(1, { marks: "", negative_marks: "" }), q(2, { marks: "2", negative_marks: "0" })] }),
      "PUT /api/v1/photo-drafts/d1/questions": (_u: URL, init: RequestInit) => draft({ extracted_at: "2030-01-01", questions: JSON.parse(String(init.body)).questions, corrections: 1 }) });
    renderApp("/papers/photo/d1");
    expect(await screen.findByText("1 question(s) have no marks on the photo. Marks for each of them:")).toBeTruthy();
    const apply = screen.getByRole("button", { name: "Apply to questions without marks" }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);                                                                    // nothing is assumed
    fireEvent.change(screen.getByLabelText("Marks for a correct answer"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Negative marks for a wrong answer"), { target: { value: "1" } });
    fireEvent.click(apply);
    fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const saved = calls.find((c) => c.method === "PUT")!.body as { questions: { marks: string; negative_marks: string }[] };
    expect(saved.questions.map((x) => [x.marks, x.negative_marks])).toEqual([["4", "1"], ["2", "0"]]);
  });
});
