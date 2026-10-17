import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
      expect([last.get("difficulty"), last.get("chapter"), last.get("include_used"), last.get("branch_id")]).toEqual(["HARD", "Laws of Motion", "false", "b1"]);
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Preview" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Question preview" });
    expect(within(dialog).getByText(/\(correct answer\)/).closest("li")!.textContent).toContain("b");
    expect(within(dialog).getByText(/Students see answers and explanations only at result review/)).toBeTruthy();
  });

  it("creates one paper per subject from a multi-subject selection, referencing the questions", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/questions": page([q("q1", "phy"), q("q2", "phy"), q("q3", "che")]),
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
    expect([body.scope, body.branch_id, body.chapter, body.marks_positive, body.subject_id]).toEqual(["GLOBAL", undefined, "Laws of Motion", "4", "phy"]);
  });
});

describe("Import Processing", () => {
  it("uploads a question file with a separate answer key and lists the result in the history", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/uploads": page([]),
      "POST /api/v1/branches/b1/subjects/phy/papers/upload": { paper_version_id: "v9", question_count: 30 } });
    renderApp("/papers?tab=imports");
    await screen.findByRole("option", { name: "Physics" });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Mock 4" } });
    fireEvent.change(screen.getByLabelText(/Question file/), { target: { files: [new File(["q"], "paper.docx")] } });
    fireEvent.click(screen.getByLabelText(/separate answer-key file/));
    fireEvent.change(await screen.findByLabelText(/Answer key/), { target: { files: [new File(["1,A"], "key.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Validate & import" }));
    expect(await screen.findByText(/30 questions validated/)).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ file: "paper.docx", title: "Mock 4", answer_key: "key.csv" });
  });

  it("shows every problem of a rejected upload and that nothing was imported", async () => {
    stubApi({ ...common, "/api/v1/auth/me": PA,
      "/api/v1/uploads": page([{ upload_id: "u1", title: "Bad", subject_id: "phy", status: "REJECTED", question_count: 0, error_count: 1, has_answer_key: true,
        errors: [{ row: null, field: "correct", message: "Answer mismatch for question 2: the paper says B, the answer key says C." }],
        paper_version_id: null, paper_version_status: null, version_no: null, created_at: "2026-10-17T10:00:00Z" }]),
      "POST /api/v1/branches/b1/subjects/phy/papers/upload": fail(422, { code: "EXTRACTION_FAILED", message: "rejected", errors: [{ row: 3, field: "option_b", message: "Option B is empty." }] }) });
    renderApp("/papers?tab=imports");
    await screen.findByRole("option", { name: "Physics" });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "phy" } });
    fireEvent.change(screen.getByLabelText("Paper title"), { target: { value: "Mock 5" } });
    fireEvent.change(screen.getByLabelText(/Question file/), { target: { files: [new File(["q"], "paper.csv")] } });
    fireEvent.click(screen.getByRole("button", { name: "Validate & import" }));
    expect(await screen.findByText(/No question was imported/)).toBeTruthy();
    expect(screen.getByText("Option B is empty.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "1 problem(s)" }));
    expect(await screen.findByText(/Answer mismatch for question 2/)).toBeTruthy();
  });
});
