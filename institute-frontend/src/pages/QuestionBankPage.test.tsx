import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "../services/api";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi } from "../test/stubApi";

const PROVIDER = { provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" };
const BRANCH = { branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" };
const PHY = { subject_id: "phy", code: "PHYSICS", name: "Physics", status: "ACTIVE" };
const CHE = { subject_id: "che", code: "CHEM", name: "Chemistry", status: "ACTIVE" };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([PROVIDER]), "/api/v1/providers/p1/branches": page([BRANCH]),
  "/api/v1/subjects": page([PHY, CHE]), "/api/v1/providers/p1/subjects": [PHY, CHE],
  "/api/v1/question-taxonomy": { exam_type: ["NEET"], target_class: ["Class 12"], chapter: ["Laws of Motion"], topic: ["Force"], subtopic: [], difficulty: ["EASY", "MEDIUM", "HARD"], pattern: ["SINGLE_CORRECT"] } };

const q = (id: string, subject: string, extra: Record<string, unknown> = {}) => ({
  question_id: id, scope: "PROVIDER", provider_id: "p1", branch_id: "b1", subject_id: subject, status: "ACTIVE", exam_type: "NEET", target_class: "Class 12",
  chapter: "Laws of Motion", topic: "Force", subtopic: null, difficulty: "EASY", pattern: "SINGLE_CORRECT", subtype: null, question_type: "MCQ_SINGLE",
  created_at: "2026-10-01T00:00:00Z", usage_count: 0, can_edit: true,
  latest: { question_version_id: `${id}v1`, question_id: id, version_no: 1, stem: `Question ${id}?`, option_a: "a", option_b: "b", option_c: "c", option_d: "d",
    correct_option: "B", explanation: "Because.", topic: "Force", marks_positive: "4.00", marks_negative: "1.00" }, ...extra,
});
const field = (original: string, text: string, outcome: string, extra: Record<string, unknown> = {}) =>
  ({ original, text, outcome, reasons: [], spans: [], ...extra });
const CLEAN_PREVIEW = { preview: true, review_token: "tok", engine_version: "2.0.0", domain: "physics",
  summary: { questions: 1, questions_needing_review: 0, fields: { CONVERTED: 1, PLAIN_TEXT: 4, NEEDS_REVIEW: 0, EXTRACTION_ERROR: 0 } },
  questions: [{ number: 1, row: 2, correct: "A", marks: "4", negative_marks: "1", topic: null, status: "READY", fields: {
    question: field("Solve x² = 4.", "Solve \\(x^{2} = 4\\).", "CONVERTED"), option_a: field("2", "2", "PLAIN_TEXT"), option_b: field("3", "3", "PLAIN_TEXT"),
    option_c: field("4", "4", "PLAIN_TEXT"), option_d: field("5", "5", "PLAIN_TEXT") } }] };
const FLAGGED_PREVIEW = { ...CLEAN_PREVIEW, summary: { questions: 1, questions_needing_review: 1, fields: { CONVERTED: 0, PLAIN_TEXT: 4, NEEDS_REVIEW: 1, EXTRACTION_ERROR: 0 } },
  questions: [{ ...CLEAN_PREVIEW.questions[0], status: "NEEDS_REVIEW", fields: { ...CLEAN_PREVIEW.questions[0].fields,
    question: field("Evaluate ∫₀π sin x dx.", "Evaluate ∫₀π sin x dx.", "NEEDS_REVIEW", { reasons: ["BOUND_TYPED_ON_LINE"],
      spans: [{ original: "∫₀π sin x dx", status: "flagged", latex: null, code: "BOUND_TYPED_ON_LINE", domain: "mathematics",
        reason: "A limit seems to be typed on the line right after the other limit (like ∫₀π)." }] }) } }] };
const UNIT_TEXT = "18. Speed equation: \\(v=5\\,\\mathrm{m}\\,\\mathrm{s}^{-1}\\)";
const UNIT_PREVIEW = { ...FLAGGED_PREVIEW, questions: [{ ...FLAGGED_PREVIEW.questions[0], fields: { ...FLAGGED_PREVIEW.questions[0].fields,
  question: { ...field("18. Speed equation: \\(v=5 m {s}^{-1}\\)", "18. Speed equation: \\(v=5 m {s}^{-1}\\)", "NEEDS_REVIEW", { reasons: ["AMBIGUOUS_UNIT"],
    spans: [{ original: "\\(v=5 m {s}^{-1}\\)", status: "flagged", latex: "v=5 m {s}^{-1}", code: "AMBIGUOUS_UNIT", domain: "word-equation",
      reason: "In this Word equation 'm' after a number is in italic." }] }),
    suggestions: [{ expression: "\\(v=5 m {s}^{-1}\\)", label: "as units (upright)", text: UNIT_TEXT },
                  { expression: "\\(v=5 m {s}^{-1}\\)", label: "as variables (italic, as in the file)", text: "18. Speed equation: \\(v=5 m {s}^{-1}\\)" }] } } }] };
const DRAFT = { draft_id: "d1", status: "OPEN", title: "Mock", file_name: "paper.docx", subject_id: "phy", revision: 1, decisions: {},
  updated_at: "2026-11-13T10:00:00Z", result_paper_version_id: null, has_answer_key: false };
const START = "POST /api/v1/branches/b1/subjects/phy/review-drafts";
// a saved review: started with the file, decisions autosaved (PATCH), imported from the stored file
const drafting = (preview: unknown, imported: unknown = { paper_version_id: "v9", question_count: 1 }) => ({
  "/api/v1/review-drafts": { items: [] }, [START]: { draft: DRAFT, preview, resumed: false },
  "PATCH /api/v1/review-drafts/d1": (_u: URL, init: RequestInit) => ({ revision: 1 + (JSON.parse(String(init.body)) as { base_revision: number }).base_revision,
    updated_at: "2026-11-13T10:05:00Z" }),
  "POST /api/v1/review-drafts/d1/import": imported });
const SAVED = { timeout: 4000 };                                                       // autosave waits 1.5 s after the last change
const PA = me("PROVIDER_ADMIN", ["question.read", "question.manage", "paper.read", "paper.create", "paper.upload", "paper.approve", "paper.publish"]);
afterEach(() => vi.unstubAllGlobals());

describe("Question Bank", () => {
  it("filters along the taxonomy through the API and previews a question with its answer for staff", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([q("q1", "phy")]),
      "/api/v1/questions/q1": { question: q("q1", "phy"), versions: [q("q1", "phy").latest], usage: [], usage_elsewhere: 0 } });
    renderApp("/papers");
    expect(await screen.findByText("Question q1?")).toBeTruthy();
    expect(calls.find((c) => c.path === "/api/v1/questions")!.search.get("scope")).toBe("ALL");
    fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "HARD" } });
    fireEvent.change(screen.getByLabelText("Chapter"), { target: { value: "Laws of Motion" } });
    fireEvent.click(screen.getByLabelText(/Include questions already used/));
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/api/v1/questions").at(-1)!.search;
      expect([last.get("difficulty"), last.get("chapter"), last.get("include_used"), last.get("provider_id"), last.get("branch_id")])
        .toEqual(["HARD", "Laws of Motion", "false", "p1", null]);                       // "All Branches" first: the provider's whole bank
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Preview" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Question preview" });
    expect(within(dialog).getByText(/\(correct answer\)/).closest("li")!.textContent).toContain("b");
    expect(within(dialog).getByText(/Students see answers and explanations only at result review/)).toBeTruthy();
  });

  it("creates one paper per subject from a multi-subject selection, referencing the questions", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([q("q1", "phy"), q("q2", "phy"), q("q3", "che")]),
      "POST /api/v1/papers/check": { ok: true, question_count: 1, total_marks: null, review: [] },
      "POST /api/v1/papers": (_u: URL, init: RequestInit) => {
        const b = JSON.parse(init.body as string);
        return { paper_id: `p-${b.subject_id}`, paper_version_id: `v-${b.subject_id}`, version_no: 1, status: "VALIDATED", question_count: b.question_ids.length, total_marks: "8.00" };
      } });
    renderApp("/papers");
    for (const id of ["q1", "q2", "q3"]) fireEvent.click(await screen.findByLabelText(`Select question: Question ${id}?`));
    fireEvent.click(screen.getByRole("button", { name: "Create paper from selection" }));
    const dialog = await screen.findByRole("dialog", { name: "Create paper from selected questions" });
    fireEvent.change(within(dialog).getByLabelText(/Physics paper title/), { target: { value: "Physics set" } });
    fireEvent.change(within(dialog).getByLabelText(/Chemistry paper title/), { target: { value: "Chemistry set" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create 2 papers" }));
    expect(await screen.findByText(/Created/)).toBeTruthy();
    const posts = calls.filter((c) => c.method === "POST" && c.path === "/api/v1/papers").map((c) => c.body as { subject_id: string; question_ids: string[]; branch_id: string; scope: string });
    expect(posts.map((b) => [b.subject_id, b.question_ids, b.branch_id, b.scope])).toEqual([["phy", ["q1", "q2"], "b1", "PROVIDER"], ["che", ["q3"], "b1", "PROVIDER"]]);
  });

  it("shows possible duplicates before building, and builds only when asked again", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([q("q1", "phy"), q("q2", "phy", { branch_id: null })]),
      "POST /api/v1/papers/check": { ok: true, question_count: 2, total_marks: null,
        review: [{ positions: [1, 2], reason: "Same question with its options in a different order." }] },
      "POST /api/v1/papers": { paper_id: "p", paper_version_id: "v", version_no: 1, status: "VALIDATED", question_count: 2, total_marks: null } });
    renderApp("/papers");
    expect(await screen.findByText("Provider-wide")).toBeTruthy();                       // the origin of a question without a branch
    for (const id of ["q1", "q2"]) fireEvent.click(await screen.findByLabelText(`Select question: Question ${id}?`));
    fireEvent.click(screen.getByRole("button", { name: "Create paper from selection" }));
    const dialog = await screen.findByRole("dialog", { name: "Create paper from selected questions" });
    fireEvent.change(within(dialog).getByLabelText(/Physics paper title/), { target: { value: "Physics set" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create paper" }));
    expect(await within(dialog).findByText(/Same question with its options in a different order/)).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/papers")).toBe(false);          // nothing built yet
    fireEvent.click(within(dialog).getByRole("button", { name: "Create anyway" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/papers")).toBe(true));
  });

  it("names the branch a shared question came from, and previews its pictures through the authorised endpoint", async () => {
    const pictured = q("q1", "phy", { branch_id: "b2", origin_branch_name: "Thane Branch", can_edit: false,
      latest: { ...q("q1", "phy").latest, stem: "", stem_image_id: "img-stem", option_b: "", option_b_image_id: "img-b" } });
    stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([pictured]),
      "/api/v1/questions/q1": { question: pictured, versions: [pictured.latest], usage: [], usage_elsewhere: 0 } });
    renderApp("/papers");
    const row = (await screen.findByText("Thane Branch")).closest("tr")!;                  // not "Another branch"
    fireEvent.click(within(row).getByRole("button", { name: "Preview" }));
    const dialog = await screen.findByRole("dialog", { name: "Question preview" });
    expect((within(dialog).getByAltText("Question picture") as HTMLImageElement).src).toBe(`${API_URL}/api/v1/question-images/img-stem`);
    expect((within(dialog).getByAltText("Option B picture") as HTMLImageElement).src).toBe(`${API_URL}/api/v1/question-images/img-b`);
  });

  it("refuses identical questions, naming both, and offers no way around it", async () => {
    const same = q("q2", "phy", { scope: "GLOBAL", provider_id: null, branch_id: null, latest: { ...q("q1", "phy").latest, question_id: "q2" } });
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([q("q1", "phy"), same]),
      "POST /api/v1/papers/check": () => fail(422, { code: "INVALID_QUESTIONS", message: "The paper was not created: fix the listed questions.",
        errors: [{ position: 2, message: "Same question as position 1." }] }),
      "POST /api/v1/papers": { paper_id: "p", paper_version_id: "v", version_no: 1, status: "VALIDATED", question_count: 2, total_marks: null } });
    renderApp("/papers");
    await screen.findAllByText("Question q1?");
    for (const box of screen.getAllByLabelText("Select question: Question q1?")) fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: "Create paper from selection" }));
    const dialog = await screen.findByRole("dialog", { name: "Create paper from selected questions" });
    fireEvent.change(within(dialog).getByLabelText(/Physics paper title/), { target: { value: "Physics set" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create paper" }));
    expect(await within(dialog).findByText("The same question is selected twice. Unselect one question of each pair below, then create the paper.")).toBeTruthy();
    expect(within(dialog).getByText(/Question 1 of your selection/)).toBeTruthy();
    expect(within(dialog).getByText(/Question 2 of your selection/)).toBeTruthy();
    expect(within(dialog).getByText(/Identical question \(same text, options and pictures\)/)).toBeTruthy();
    expect((within(dialog).getByRole("button", { name: "Create paper" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/papers")).toBe(false);
  });

  it("lets the Super Admin add a platform question, and hides edit actions on questions it cannot change", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("SUPER_ADMIN", ["question.read", "question.manage", "paper.read", "paper.create"]),
      "/api/v1/questions": page([q("g1", "phy", { scope: "GLOBAL", provider_id: null, branch_id: null }), q("x1", "phy", { can_edit: false })]),
      "POST /api/v1/questions": { question_version_id: "n" } });
    renderApp("/papers");
    expect(((await screen.findByLabelText("Questions from")) as HTMLSelectElement).value).toBe("GLOBAL");
    expect(await screen.findByText("Question g1?")).toBeTruthy();
    const rows = screen.getAllByRole("row");
    const foreign = rows.find((r) => within(r).queryByText("Question x1?"))!;
    expect(within(foreign).queryByRole("button", { name: "Classify" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add platform question" }));
    const d = await screen.findByRole("dialog", { name: "Add a platform question" });
    fireEvent.change(within(d).getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(within(d).getByLabelText("Question"), { target: { value: "What is inertia?" } });
    ["A", "B", "C", "D"].forEach((o) => fireEvent.change(within(d).getByLabelText(`Option ${o}`), { target: { value: `opt ${o}` } }));
    fireEvent.change(within(d).getByLabelText("Chapter"), { target: { value: "Laws of Motion" } });
    fireEvent.click(within(d).getByRole("button", { name: "Add question" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/questions")).toBeTruthy());
    const body = calls.find((c) => c.method === "POST" && c.path === "/api/v1/questions")!.body as Record<string, unknown>;
    // marks are optional and no longer pre-filled: left empty, the question carries no marks and each test decides them
    expect([body.scope, body.branch_id, body.chapter, body.marks_positive, body.marks_negative, body.subject_id])
      .toEqual(["GLOBAL", undefined, "Laws of Motion", null, null, "phy"]);
  });
});

describe("Classify: chapter names", () => {
  it("suggests the official name while typing, applies it only on a click, and can ask to remember the spelling", async () => {
    const SUGGEST = { match: null, matched_by: null, preselect: "n1", in_use: true, elsewhere: [],
      suggestions: [{ node_id: "n1", name: "Laws of Motion", band: "HIGH", matched: "Laws of Motion", score: 0.9 }] };
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([q("q1", "phy")]),
      "/api/v1/question-taxonomy/suggest": (url: URL) => (url.searchParams.get("level") !== "CHAPTER"
        ? { ...SUGGEST, suggestions: [], preselect: null, in_use: false }
        : url.searchParams.get("value") === "Laws of Motion"
          ? { ...SUGGEST, match: { node_id: "n1", name: "Laws of Motion" }, matched_by: "NAME", suggestions: [], preselect: null } : SUGGEST),
      "POST /api/v1/question-taxonomy/alias-requests": { approved: false },
      "PATCH /api/v1/questions/q1": q("q1", "phy") });
    renderApp("/papers");
    fireEvent.click(await screen.findByRole("button", { name: "Classify" }));
    const d = await screen.findByRole("dialog", { name: "Classify question" });
    fireEvent.change(within(d).getByLabelText("Chapter"), { target: { value: "Newtons Law of Motions" } });
    fireEvent.click(await within(d).findByRole("button", { name: "Laws of Motion" }, { timeout: 2000 }));
    expect((within(d).getByLabelText("Chapter") as HTMLInputElement).value).toBe("Laws of Motion");
    const s = calls.filter((c) => c.path === "/api/v1/question-taxonomy/suggest" && c.search.get("level") === "CHAPTER").at(-1)!.search;
    expect([s.get("provider_id"), s.get("subject_id"), s.get("level"), s.get("value")]).toEqual(["p1", "phy", "CHAPTER", "Newtons Law of Motions"]);
    fireEvent.click(within(d).getByRole("button", { name: "Remember “Newtons Law of Motions” as another spelling" }));
    expect(await within(d).findByText(/Your administrator was asked to accept/)).toBeTruthy();
    expect(calls.find((c) => c.path === "/api/v1/question-taxonomy/alias-requests")?.body)
      .toEqual({ node_id: "n1", alias: "Newtons Law of Motions", source: "CLASSIFY" });
  });
});

describe("All Branches is the first choice of the Question Bank & Papers tabs", () => {
  it.each([["/papers"], ["/papers?tab=papers"], ["/papers?tab=imports"]])("%s starts on All Branches and asks the provider, not a branch", async (path) => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([]), "/api/v1/papers": page([]), "/api/v1/uploads": page([]),
      "/api/v1/review-drafts": { items: [] } });
    renderApp(path);
    const branch = await screen.findByLabelText("Branch", { selector: ".scope-bar select" });
    expect((branch as HTMLSelectElement).selectedOptions[0].textContent).toBe("All Branches");
    const listed = ["/api/v1/questions", "/api/v1/papers", "/api/v1/uploads"];
    await waitFor(() => expect(calls.some((c) => listed.includes(c.path))).toBe(true));
    for (const c of calls.filter((x) => listed.includes(x.path) || x.path === "/api/v1/review-drafts")) {
      expect([c.search.get("provider_id"), c.search.get("branch_id")]).toEqual(["p1", null]);
    }
  });

  it("asks which branch a new question, paper or import belongs to while All Branches is shown", async () => {
    const admin = { ...PA, assignments: [{ role: "PROVIDER_ADMIN", provider_id: "p1", branch_id: null }] };
    stubApi({ ...common, "/api/v1/auth/me": admin, "/api/v1/questions": page([]), "/api/v1/uploads": page([]), "/api/v1/review-drafts": { items: [] } });
    renderApp("/papers");
    fireEvent.click(await screen.findByRole("button", { name: "Add question" }));
    const dialog = await screen.findByRole("dialog");
    expect((within(dialog).getByLabelText("Branch") as HTMLSelectElement).value).toBe("b1");
    fireEvent.click(within(dialog).getByLabelText(/Provider-wide/));                              // a provider-wide question has no branch
    expect(within(dialog).queryByLabelText("Branch")).toBeNull();
  });
});

describe("Import Processing", () => {
  it("previews a question file with a separate answer key, then imports it", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/uploads": page([]),
      ...drafting(CLEAN_PREVIEW, { paper_version_id: "v9", question_count: 30 }) });
    renderApp("/papers?tab=imports");
    await screen.findByRole("option", { name: "Physics" });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Mock 4" } });
    fireEvent.change(screen.getByLabelText(/Question file/), { target: { files: [new File(["q"], "paper.docx")] } });
    fireEvent.click(screen.getByLabelText(/separate answer-key file/));
    fireEvent.change(await screen.findByLabelText(/Answer key/), { target: { files: [new File(["1,A"], "key.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Validate & preview" }));
    expect(await screen.findByText("Preview before import")).toBeTruthy();
    expect(document.querySelector(".katex")).toBeTruthy();                                    // the converted stem is typeset
    fireEvent.click(screen.getByRole("button", { name: "Import as a draft paper" }));
    expect(await screen.findByText(/30 questions validated/)).toBeTruthy();
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts[0].body).toEqual({ file: "paper.docx", title: "Mock 4", answer_key: "key.csv" });     // the file is kept with the review
    expect([posts[1].path, posts[1].body]).toEqual(["/api/v1/review-drafts/d1/import", { revision: 1 }]);   // imported from it, not sent again
  });

  it("offers the readings of a unit-or-variable expression; choosing one fills the correction, which is sent as the decision", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/uploads": page([]), ...drafting(UNIT_PREVIEW),
      // the correction's live preview: the server recognises it as the import would
      "POST /api/v1/branches/b1/subjects/phy/recognition-preview": (_u: URL, init: RequestInit) => {
        const { text } = JSON.parse(String(init.body)) as { text: string };
        return { original: text, text, outcome: "CONVERTED", reasons: [], spans: [], suggestions: [] };
      },
    });
    renderApp("/papers?tab=imports");
    await screen.findByRole("option", { name: "Physics" });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Units" } });
    fireEvent.change(screen.getByLabelText(/Question file/), { target: { files: [new File(["q"], "paper.docx")] } });
    fireEvent.click(screen.getByRole("button", { name: "Validate & preview" }));
    const field = await screen.findByTestId("rev-1-question");
    const choices = within(field).getByRole("group", { name: /How should .* be read/ });
    expect(choices.querySelectorAll(".katex").length).toBe(2);                              // both readings typeset, side by side
    expect((screen.getByRole("button", { name: "1 field(s) still need a decision" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(choices).getByRole("button", { name: /as units/ }));
    expect((within(field).getByLabelText(/Corrected text/) as HTMLTextAreaElement).value).toBe(UNIT_TEXT);
    await waitFor(() => expect(field.querySelector(".recognition-preview .katex")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: "Import as a draft paper" }, SAVED));
    expect(await screen.findByText(/1 questions validated/)).toBeTruthy();
    const saved = calls.filter((c) => c.method === "PATCH").map((c) => c.body);
    expect(saved).toEqual([{ base_revision: 1, changes: { "1:question": { action: "edit", text: UNIT_TEXT } } }]);
    expect(calls.find((c) => c.path.endsWith("/import"))!.body).toEqual({ revision: 2 });
  });

  it("does not import until every flagged expression has a decision, and sends the decisions", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/uploads": page([]), ...drafting(FLAGGED_PREVIEW),
      // the correction's live preview: the server recognises it as the import would
      "POST /api/v1/branches/b1/subjects/phy/recognition-preview": (_u: URL, init: RequestInit) => {
        const { text } = JSON.parse(String(init.body)) as { text: string };
        return { original: text, text, outcome: "CONVERTED", reasons: [], spans: [], suggestions: [] };
      },
    });
    renderApp("/papers?tab=imports");
    await screen.findByRole("option", { name: "Physics" });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Calculus" } });
    fireEvent.change(screen.getByLabelText(/Question file/), { target: { files: [new File(["q"], "paper.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Validate & preview" }));
    expect(await screen.findByText(/A limit seems to be typed on the line/)).toBeTruthy();
    expect(screen.getByText("∫₀π sin x dx", { selector: "code" })).toBeTruthy();                 // the original, never a guess
    const importButton = screen.getByRole("button", { name: "1 field(s) still need a decision" });
    expect((importButton as HTMLButtonElement).disabled).toBe(true);
    const field = screen.getByTestId("rev-1-question");
    fireEvent.click(within(field).getByLabelText("Correct it"));
    fireEvent.change(within(field).getByLabelText(/Corrected text/), { target: { value: "Evaluate \\(\\int_0^\\pi \\sin x\\,dx\\)." } });
    await waitFor(() => expect(field.querySelector(".recognition-preview .katex")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: "Import as a draft paper" }, SAVED));
    expect(await screen.findByText(/1 questions validated/)).toBeTruthy();
    const saved = calls.filter((c) => c.method === "PATCH").at(-1)!.body as { changes: Record<string, unknown> };
    expect(saved.changes).toEqual({ "1:question": { action: "edit", text: "Evaluate \\(\\int_0^\\pi \\sin x\\,dx\\)." } });
  });

  it("reopens a saved review after a reload, and lists the reviews in progress to continue", async () => {
    const saved = { ...DRAFT, title: "Calculus", revision: 3,
      decisions: { "1:question": { decision: { action: "keep_original" }, rev: 3, by: "me", at: "2026-11-13T10:04:00Z" } } };
    stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/uploads": page([]),
      "/api/v1/review-drafts": { items: [{ ...saved, decided: 1 }] }, "/api/v1/review-drafts/d1": { draft: saved, preview: FLAGGED_PREVIEW } });
    renderApp("/papers?tab=imports&draft=d1");                                                // the address the review left after a reload
    const field = await screen.findByTestId("rev-1-question");
    expect((within(field).getByLabelText(/Keep the text exactly/) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("button", { name: "Import as a draft paper" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Close (continue later)" }));
    const list = (await screen.findByRole("heading", { name: "Reviews in progress" })).closest(".card") as HTMLElement;
    expect(within(list).getByText("Calculus")).toBeTruthy();
    fireEvent.click(within(list).getByRole("button", { name: "Continue" }));
    expect(await screen.findByTestId("rev-1-question")).toBeTruthy();
  });

  it("refuses a correction that KaTeX cannot display", async () => {
    stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/uploads": page([]), ...drafting(FLAGGED_PREVIEW) });
    renderApp("/papers?tab=imports");
    await screen.findByRole("option", { name: "Physics" });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Calculus" } });
    fireEvent.change(screen.getByLabelText(/Question file/), { target: { files: [new File(["q"], "paper.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Validate & preview" }));
    const field = await screen.findByTestId("rev-1-question");
    fireEvent.click(within(field).getByLabelText("Correct it"));
    fireEvent.change(within(field).getByLabelText(/Corrected text/), { target: { value: "\\(\\frac{1}{\\)" } });
    expect((await screen.findByRole("button", { name: "1 field(s) still need a decision" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(field).getByLabelText(/Keep the text exactly as in the file/));
    expect((await screen.findByRole("button", { name: "Import as a draft paper" }, SAVED) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows every problem of a rejected upload and that nothing was imported", async () => {
    stubApi({ ...common, "/api/v1/auth/me": PA,
      "/api/v1/uploads": page([{ upload_id: "u1", title: "Bad", subject_id: "phy", status: "REJECTED", question_count: 0, error_count: 1, has_answer_key: true,
        errors: [{ row: null, field: "correct", message: "Answer mismatch for question 2: the paper says B, the answer key says C." }],
        paper_version_id: null, paper_version_status: null, version_no: null, created_at: "2026-10-17T10:00:00Z" }]),
      "/api/v1/review-drafts": { items: [] },
      [START]: fail(422, { code: "EXTRACTION_FAILED", message: "rejected", errors: [{ row: 3, field: "option_b", message: "Option B is empty." }] }) });
    renderApp("/papers?tab=imports");
    await screen.findByRole("option", { name: "Physics" });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Mock 5" } });
    fireEvent.change(screen.getByLabelText(/Question file/), { target: { files: [new File(["q"], "paper.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Validate & preview" }));
    expect(await screen.findByText(/No question was imported/)).toBeTruthy();
    expect(screen.getByText("Option B is empty.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "1 problem(s)" }));
    expect(await screen.findByText(/Answer mismatch for question 2/)).toBeTruthy();
  });
});
