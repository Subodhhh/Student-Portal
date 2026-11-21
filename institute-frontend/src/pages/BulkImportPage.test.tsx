import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "../services/api";
import { renderApp } from "../test/renderApp";
import { fail, me, page, stubApi, type Call } from "../test/stubApi";

/* The Bulk Import page against stubbed API responses shaped exactly like the paper service's (routes/bulk_imports.py). */

const PROVIDER = { provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" };
const BRANCH = { branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" };
const PHY = { subject_id: "phy", code: "PHYSICS", name: "Physics", status: "ACTIVE" };
const ADMIN = me("PROVIDER_ADMIN", ["paper.read", "question.read", "question.manage", "taxonomy.manage"]);
const TEACHER = me("TEACHER", ["paper.read", "question.read", "question.manage"]);
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([PROVIDER]),
  "/api/v1/providers/p1/branches": page([BRANCH]), "/api/v1/providers/p1/subjects": [PHY], "/api/v1/subjects": page([PHY]) };

const job = (status: string, extra: Record<string, unknown> = {}) => ({
  job_id: "j1", provider_id: "p1", branch_id: "b1", subject_id: "phy", status, file_format: "XLSX", file_name: "physics.xlsx",
  total_rows: 3, valid_rows: 1, invalid_rows: 1, duplicate_rows: 1, imported_rows: 0, failed_rows: 0, progress: { done: 3, total: 3 },
  file_errors: null, last_error: null, created_by: "me", created_at: "2026-11-20T10:00:00Z", confirmed_by: null, confirmed_at: null,
  finished_at: null, ...extra });
const data = (over: Record<string, string> = {}) => ({ chapter: "Laws of Motion", topic: "Force", subtopic: "", difficulty: "Easy", question_type: "MCQ",
  question_text: "What is the SI unit of force?", option_a_text: "Newton", option_b_text: "Joule", option_c_text: "Watt", option_d_text: "Pascal",
  correct_option: "A", ...over });
const VALID_ROW = { row_no: 2, status: "VALID", data: data(), errors: [], images: {}, content_format: "TEXT", question_id: null };
const PICTURE_ROW = { row_no: 3, status: "INVALID", data: data({ question_text: "", option_b_text: "", difficulty: "Impossible", chapter: "Newton Laws" }),
  errors: [{ field: "difficulty", message: "Difficulty must be Easy, Medium or Hard, not “Impossible”." },
    { field: "chapter", message: "“Newton Laws” is not a chapter of this subject yet.", code: "TAXONOMY_UNMATCHED", level: "CHAPTER", value: "Newton Laws",
      parent_id: null, suggestions: [{ node_id: "n1", name: "Laws of Motion" }] }],
  images: { question_image: { sha256: "aa", url: "/api/v1/bulk-imports/j1/images/aa" }, option_b_image: { sha256: "bb", url: "/api/v1/bulk-imports/j1/images/bb" } },
  content_format: "IMAGE", question_id: null };
const DUPLICATE_ROW = { ...VALID_ROW, row_no: 4, status: "DUPLICATE", errors: [{ field: null, message: "Same question as row 2." }] };
const ISSUE = { level: "CHAPTER", value: "Newton Laws", parent_id: null, suggestions: [{ node_id: "n1", name: "Laws of Motion" }], rows: 1, example_rows: [3] };
const NODES = [{ node_id: "n1", parent_id: null, level: "CHAPTER", name: "Laws of Motion", status: "ACTIVE" },
  { node_id: "n2", parent_id: null, level: "CHAPTER", name: "Optics", status: "ACTIVE" }];

/** A preview of job j1 (`status` and extra fields), with its rows, unmatched chapter and taxonomy records. */
function preview(status = "PREVIEW", extra: Record<string, unknown> = {}, routes: Record<string, unknown> = {}) {
  return stubApi({ ...common, "/api/v1/auth/me": ADMIN, "/api/v1/bulk-imports/j1": job(status, extra),
    "/api/v1/bulk-imports/j1/rows": (url: URL) => page([VALID_ROW, PICTURE_ROW, DUPLICATE_ROW].filter((r) => !url.searchParams.get("status") || r.status === url.searchParams.get("status"))),
    "/api/v1/bulk-imports/j1/taxonomy-issues": [ISSUE], "/api/v1/question-taxonomy/nodes": NODES, ...routes });
}

// ------------------------------------------------------------------ upload (XMLHttpRequest, for progress)

type Sent = { url: string; fields: Record<string, string> };
const sent: Sent[] = [];
let xhrAnswer: { status: number; body: unknown } | "network" = { status: 202, body: job("UPLOADED") };

class FakeXhr {
  status = 0;
  responseText = "";
  upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  withCredentials = false;
  private url = "";
  open(_method: string, url: string) { this.url = url; }
  setRequestHeader() {}
  send(form: FormData) {
    sent.push({ url: this.url, fields: Object.fromEntries([...form.entries()].map(([k, v]) => [k, typeof v === "string" ? v : (v as File).name])) });
    setTimeout(() => {
      if (xhrAnswer === "network") { this.onerror?.(); return; }
      this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
      this.status = xhrAnswer.status;
      this.responseText = JSON.stringify(xhrAnswer.body);
      this.onload?.();
    }, 0);
  }
}

const saved: string[] = [];
beforeEach(() => {
  sent.length = 0;
  saved.length = 0;
  xhrAnswer = { status: 202, body: job("UPLOADED") };
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  URL.createObjectURL = vi.fn(() => "blob:report");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download); });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function startPage(user = ADMIN, extra: Record<string, unknown> = {}) {
  const calls = stubApi({ ...common, "/api/v1/auth/me": user, "/api/v1/bulk-imports": page([job("COMPLETED", { imported_rows: 3, file_name: "earlier-upload.xlsx" })]),
    "/api/v1/bulk-imports/j1": job("PREVIEW"), "/api/v1/bulk-imports/j1/rows": page([VALID_ROW]),
    "/api/v1/bulk-imports/j1/taxonomy-issues": [], ...extra });
  renderApp("/papers/bulk-import");
  return calls;
}

async function chooseFile(name: string, size?: number) {
  const input = await screen.findByLabelText("File (.xlsx or .docx)");
  const file = new File(["PK"], name);
  if (size !== undefined) Object.defineProperty(file, "size", { value: size });
  fireEvent.change(input, { target: { files: [file] } });
}

async function chooseSubject() {
  const select = await screen.findByLabelText("Subject");
  await waitFor(() => expect(within(select).getByRole("option", { name: "Physics" })).toBeTruthy());
  fireEvent.change(select, { target: { value: "phy" } });
}

describe("Bulk Import page: upload", () => {
  it.each([["physics.xlsx"], ["physics.docx"]])("uploads %s with branch and subject, then opens the import", async (name) => {
    startPage();
    await chooseSubject();
    await chooseFile(name);
    expect(screen.getByText(name)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    await waitFor(() => expect(sent).toEqual([{ url: `${API_URL}/api/v1/bulk-imports`, fields: { branch_id: "b1", subject_id: "phy", file: name } }]));
    expect(await screen.findByText("Ready for review")).toBeTruthy();                       // navigated to the import (stubbed PREVIEW)
  });

  it.each([
    ["questions.csv", undefined, "CSV and .doc files are not supported"],
    ["questions.doc", undefined, "CSV and .doc files are not supported"],
    ["huge.xlsx", 600 * 1024 * 1024, "larger than 512 MB"],
  ])("refuses %s before sending anything", async (name, size, message) => {
    startPage();
    await chooseSubject();
    await chooseFile(name, size);
    expect(await screen.findByText(new RegExp(message))).toBeTruthy();
    expect((screen.getByRole("button", { name: "Upload and check" }) as HTMLButtonElement).disabled).toBe(true);
    expect(sent).toEqual([]);
  });

  it("needs a subject, and can remove the chosen file", async () => {
    startPage();
    await chooseFile("physics.xlsx");
    expect(screen.getByText("Choose the subject of these questions.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Upload and check" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.queryByText("physics.xlsx")).toBeNull();
  });

  it("asks for a branch when none is available", async () => {
    stubApi({ ...common, "/api/v1/auth/me": ADMIN, "/api/v1/providers/p1/branches": page([]) });
    renderApp("/papers/bulk-import");
    expect(await screen.findByText("No branch available")).toBeTruthy();
    expect(screen.queryByLabelText("File (.xlsx or .docx)")).toBeNull();
  });

  it("shows the server's refusal and a network failure", async () => {
    xhrAnswer = { status: 422, body: { detail: { code: "INVALID_FILE", message: "The file is not a valid Excel (.xlsx) or Word (.docx) file." } } };
    startPage();
    await chooseSubject();
    await chooseFile("physics.xlsx");
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    expect(await screen.findByText("The file is not a valid Excel (.xlsx) or Word (.docx) file.")).toBeTruthy();
    xhrAnswer = "network";
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    expect(await screen.findByText(/Cannot reach the server/)).toBeTruthy();
  });

  it("lists the branch's earlier imports with subject, status and counts", async () => {
    const calls = startPage();
    const link = await screen.findByRole("link", { name: "earlier-upload.xlsx" });
    expect(link.getAttribute("href")).toBe("/papers/bulk-import/j1");
    const row = link.closest("tr")!;
    expect(within(row).getByText("Physics")).toBeTruthy();
    expect(within(row).getByText("COMPLETED")).toBeTruthy();
    expect(within(row).getByText("3 read · 1 ready · 3 imported")).toBeTruthy();
    expect(calls.find((c) => c.path === "/api/v1/bulk-imports")!.search.get("branch_id")).toBe("b1");
  });

  it("is not offered to staff who may not add questions", async () => {
    stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["paper.read", "question.read"]) });
    renderApp("/papers/bulk-import");
    await waitFor(() => expect(screen.queryByLabelText("File (.xlsx or .docx)")).toBeNull());
    expect(await screen.findByText(/not allowed|permission|access/i)).toBeTruthy();
  });
});

describe("Bulk Import page: processing and preview", () => {
  it("refreshes a job the worker is processing until its preview is ready", async () => {
    let calls = 0;
    preview("PREVIEW", {}, { "/api/v1/bulk-imports/j1": () => (++calls === 1 ? job("PARSING", { total_rows: 0, valid_rows: 0, invalid_rows: 0, duplicate_rows: 0 }) : job("PREVIEW")) });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/being read and checked/)).toBeTruthy();
    expect(await screen.findByText("Ready for review", {}, { timeout: 6000 })).toBeTruthy();
    expect(calls).toBe(2);
  }, 10000);

  it("keeps refreshing while the job waits for the worker with nothing changing", async () => {
    let calls = 0;
    const waiting = job("IMPORT_QUEUED", { confirmed_at: "2026-11-20T10:05:00Z", progress: { done: 0, total: 1 } });
    preview("PREVIEW", {}, { "/api/v1/bulk-imports/j1": () => (++calls <= 3 ? waiting : job("COMPLETED", { imported_rows: 1, confirmed_at: "2026-11-20T10:05:00Z" })) });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/Adding questions to the Question Bank/)).toBeTruthy();
    expect(await screen.findByText("All 1 question(s) were added to the Question Bank.", {}, { timeout: 14000 })).toBeTruthy();
    expect(calls).toBe(4);                                   // three identical answers did not stop the refreshing
  }, 20000);

  it("shows each question with its pictures, status and problems", async () => {
    preview();
    renderApp("/papers/bulk-import/j1");
    const invalid = await screen.findByRole("listitem", { name: "Row 3" });
    expect(invalid.className).toContain("problem");
    expect(within(invalid).getByText(/Difficulty must be Easy, Medium or Hard/)).toBeTruthy();
    expect(within(invalid).getByText("Chapter/topic to resolve")).toBeTruthy();
    const question = within(invalid).getByAltText("Row 3: question picture") as HTMLImageElement;
    expect(question.src).toBe(`${API_URL}/api/v1/bulk-imports/j1/images/aa`);                 // the authenticated endpoint, never storage
    expect((within(invalid).getByAltText("Row 3: option B picture") as HTMLImageElement).src).toBe(`${API_URL}/api/v1/bulk-imports/j1/images/bb`);
    const valid = screen.getByRole("listitem", { name: "Row 2" });
    expect(valid.className).not.toContain("problem");
    expect(within(valid).getByText("What is the SI unit of force?")).toBeTruthy();
    expect(within(valid).getByText(/✓ correct/).closest("li")!.textContent).toContain("Newton");
    expect(within(valid).getByText(/Laws of Motion › Force · Easy · MCQ · Text/)).toBeTruthy();
    expect(screen.getByRole("listitem", { name: "Row 4" }).className).toContain("duplicate");
  });

  it("filters to the rows that need correcting (one page at a time)", async () => {
    const calls = preview();
    renderApp("/papers/bulk-import/j1");
    fireEvent.click(await screen.findByRole("tab", { name: "Need correcting (1)" }));
    await waitFor(() => expect(screen.queryByRole("listitem", { name: "Row 2" })).toBeNull());
    const asked = calls.filter((c) => c.path === "/api/v1/bulk-imports/j1/rows").at(-1)!;
    expect([asked.search.get("status"), asked.search.get("page_size")]).toEqual(["INVALID", "25"]);
  });

  it("explains an unavailable or another provider's import", async () => {
    stubApi({ ...common, "/api/v1/auth/me": ADMIN, "/api/v1/bulk-imports/j1": fail(404, { code: "NOT_FOUND", message: "Import not found." }) });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText("This import is not available")).toBeTruthy();
  });

  it("explains an expired preview and offers no actions", async () => {
    preview("EXPIRED");
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/preview expired/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Cancel import|Upload \d/ })).toBeNull();
    expect(screen.queryByRole("listitem", { name: "Row 2" })).toBeNull();
  });
});

describe("Bulk Import page: corrections", () => {
  it("corrects a row's text and checks it again", async () => {
    const calls = preview("PREVIEW", {}, { "PATCH /api/v1/bulk-imports/j1/rows/3": { ...PICTURE_ROW, status: "VALID", errors: [] } });
    renderApp("/papers/bulk-import/j1");
    fireEvent.click(within(await screen.findByRole("listitem", { name: "Row 3" })).getByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Difficulty"), { target: { value: "Medium" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save and check again" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ difficulty: "Medium" }));   // only what changed
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("lets a taxonomy manager approve an alias, and checks the preview again", async () => {
    const calls = preview("PREVIEW", {}, { "POST /api/v1/bulk-imports/j1/taxonomy-resolutions": job("VALIDATE_QUEUED"),
      "POST /api/v1/bulk-imports/j1/revalidate": job("VALIDATE_QUEUED") });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/Chapter “Newton Laws”/)).toBeTruthy();
    const select = await screen.findByLabelText("Existing chapter");
    await waitFor(() => expect(within(select).getByRole("option", { name: "Laws of Motion (suggested)" })).toBeTruthy());
    expect((select as HTMLSelectElement).value).toBe("n1");                                    // preselected suggestion, not applied yet
    expect(calls.some((c) => c.path.endsWith("taxonomy-resolutions"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Always accept this name" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("taxonomy-resolutions"))?.body)
      .toEqual({ level: "CHAPTER", value: "Newton Laws", parent_id: null, action: "ALIAS", node_id: "n1" }));
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/revalidate"))).toBe(true));
  });

  it("shows other staff what must be resolved without offering the resolution", async () => {
    preview("PREVIEW", {}, { "/api/v1/auth/me": TEACHER });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/Ask a provider administrator/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Always accept this name" })).toBeNull();
  });

  it("shows the server's refusal of a resolution", async () => {
    preview("PREVIEW", {}, { "POST /api/v1/bulk-imports/j1/taxonomy-resolutions": fail(403, { code: "FORBIDDEN", message: "You are not allowed to do this." }) });
    renderApp("/papers/bulk-import/j1");
    fireEvent.click(await screen.findByRole("button", { name: "Create “Newton Laws” as a new chapter" }));
    expect(await screen.findByText("You are not allowed to do this.")).toBeTruthy();
  });
});

describe("Bulk Import page: confirmation and results", () => {
  it("confirms once after showing what will happen", async () => {
    let confirmations = 0;
    const calls = preview("PREVIEW", {}, { "POST /api/v1/bulk-imports/j1/confirm": () => { confirmations += 1; return job("IMPORT_QUEUED"); } });
    renderApp("/papers/bulk-import/j1");
    fireEvent.click(await screen.findByRole("button", { name: "Upload 1 question to the Question Bank" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("1 of 3 questions will be added to the Question Bank.")).toBeTruthy();
    expect(within(dialog).getByText(/1 question\(s\) still need correcting/)).toBeTruthy();
    expect(within(dialog).getByText(/1 duplicate\(s\) will be skipped/)).toBeTruthy();
    const confirm = within(dialog).getByRole("button", { name: "Upload 1 question" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);                                                               // a double click sends one request
    await waitFor(() => expect(confirmations).toBe(1));
    expect(calls.filter((c) => c.method === "POST" && c.path.endsWith("/confirm"))).toHaveLength(1);
  });

  it("shows why the server refuses a confirmation", async () => {
    preview("PREVIEW", {}, { "POST /api/v1/bulk-imports/j1/confirm": fail(409, { code: "NOT_CONFIRMABLE", message: "There is nothing valid to import yet; correct the rows first." }) });
    renderApp("/papers/bulk-import/j1");
    fireEvent.click(await screen.findByRole("button", { name: "Upload 1 question to the Question Bank" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Upload 1 question" }));
    expect(await screen.findByText("There is nothing valid to import yet; correct the rows first.")).toBeTruthy();
  });

  it("offers no upload while nothing is ready", async () => {
    preview("PREVIEW", { valid_rows: 0, invalid_rows: 2 });
    renderApp("/papers/bulk-import/j1");
    expect(((await screen.findByRole("button", { name: "Upload 0 questions to the Question Bank" })) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each([
    ["COMPLETED", { imported_rows: 3, valid_rows: 0, invalid_rows: 0, duplicate_rows: 0 }, "All 3 question(s) were added to the Question Bank."],
    ["PARTIAL", { imported_rows: 1, valid_rows: 0, failed_rows: 1 }, /1 question\(s\) were added\. 3 were not/],
    ["FAILED", { imported_rows: 0, file_errors: ["Unknown column(s) in the header row: “Marks”."] }, /No question was imported\. Unknown column/],
  ])("shows a %s import with the server's counts", async (status, extra, message) => {
    preview(status, { confirmed_at: "2026-11-20T10:05:00Z", finished_at: "2026-11-20T10:06:00Z", ...extra });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel import" })).toBeNull();               // ended: nothing to cancel
    expect(screen.queryByRole("button", { name: /Upload \d+ question/ })).toBeNull();
  });

  it("downloads the problem report", async () => {
    preview("PARTIAL", { imported_rows: 1, confirmed_at: "2026-11-20T10:05:00Z" });
    const json = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (!input.endsWith("/report.csv")) return json(input, init);
      return { ok: true, status: 200, headers: new Headers({ "Content-Disposition": 'attachment; filename="import-report-j1.csv"' }),
        blob: async () => new Blob(["Row,Status"]) } as Response;
    }));
    renderApp("/papers/bulk-import/j1");
    fireEvent.click(await screen.findByRole("button", { name: "Download the problem report (CSV)" }));
    await waitFor(() => expect(saved).toEqual(["import-report-j1.csv"]));
  });

  it("cancels a preview after asking", async () => {
    const calls = preview("PREVIEW", {}, { "POST /api/v1/bulk-imports/j1/cancel": job("CANCELLED") });
    renderApp("/papers/bulk-import/j1");
    fireEvent.click(await screen.findByRole("button", { name: "Cancel import" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel import" }));
    await waitFor(() => expect(calls.some((c: Call) => c.method === "POST" && c.path.endsWith("/cancel"))).toBe(true));
  });
});

// ------------------------------------------------------------------ three targets: platform, provider-wide, branch

const CHEM = { subject_id: "chem", name: "Chemistry" };
const SUPER = me("SUPER_ADMIN", ["paper.read", "question.read", "question.manage", "taxonomy.manage"]);
const TARGETS_PA = { global: null, provider_wide: [{ provider_id: "p1", name: "Alpha", subjects: [{ subject_id: "phy", name: "Physics" }] }], branch: true };
const TARGETS_SA = { global: { subjects: [{ subject_id: "phy", name: "Physics" }, CHEM] }, provider_wide: [], branch: true };

async function into(label: RegExp) {
  const select = await screen.findByLabelText("Import into");
  const option = await waitFor(() => within(select).getByRole("option", { name: label }) as HTMLOptionElement);
  fireEvent.change(select, { target: { value: option.value } });
}

describe("Bulk Import page: where the questions go", () => {
  it("offers only what the server allows: a teacher gets the branch import without a choice", async () => {
    startPage(TEACHER, { "/api/v1/bulk-imports/targets": { global: null, provider_wide: [], branch: true } });
    expect(await screen.findByLabelText("File (.xlsx or .docx)")).toBeTruthy();
    expect(screen.queryByLabelText("Import into")).toBeNull();
  });

  it("lets a Provider Admin import provider-wide: no branch is sent, the provider is", async () => {
    const calls = startPage(ADMIN, { "/api/v1/bulk-imports/targets": TARGETS_PA });
    await into(/Provider-wide — Alpha/);
    expect(screen.queryByText("No branch available")).toBeNull();
    await chooseSubject();
    await chooseFile("physics.xlsx");
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    await waitFor(() => expect(sent).toEqual([{ url: `${API_URL}/api/v1/bulk-imports`,
      fields: { target: "PROVIDER", provider_id: "p1", subject_id: "phy", file: "physics.xlsx" } }]));
    expect(calls.some((c) => c.path === "/api/v1/bulk-imports" && c.search.get("provider_id") === "p1" && !c.search.get("branch_id"))).toBe(true);
  });

  it("lets a platform administrator import into the platform bank with every platform subject", async () => {
    const calls = startPage(SUPER, { "/api/v1/bulk-imports/targets": TARGETS_SA });
    await into(/Platform question bank/);
    const select = await screen.findByLabelText("Subject");
    expect(within(select).getByRole("option", { name: "Chemistry" })).toBeTruthy();
    fireEvent.change(select, { target: { value: "chem" } });
    await chooseFile("chemistry.docx");
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    await waitFor(() => expect(sent).toEqual([{ url: `${API_URL}/api/v1/bulk-imports`, fields: { target: "GLOBAL", subject_id: "chem", file: "chemistry.docx" } }]));
    expect(calls.some((c) => c.path === "/api/v1/bulk-imports" && c.search.get("target") === "GLOBAL")).toBe(true);
  });

  it("never asks to resolve chapters of a platform import (they stay as typed)", async () => {
    const calls = preview("PREVIEW", { scope: "GLOBAL", provider_id: null, branch_id: null });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/into the platform question bank/)).toBeTruthy();
    expect(calls.some((c) => c.path.endsWith("/taxonomy-issues"))).toBe(false);
  });
});
