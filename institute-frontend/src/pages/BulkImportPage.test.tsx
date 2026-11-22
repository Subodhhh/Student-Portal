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
const ISSUE = { level: "CHAPTER", value: "Newton Laws", parent_id: null, rows: 1, example_rows: [3], preselect: "n1", may_manage: true,
  suggestions: [{ node_id: "n1", name: "Laws of Motion", band: "HIGH", matched: "Laws of Motion" }], elsewhere: [] };
const NODES = [{ node_id: "n1", parent_id: null, level: "CHAPTER", name: "Laws of Motion", status: "ACTIVE" },
  { node_id: "n2", parent_id: null, level: "CHAPTER", name: "Optics", status: "ACTIVE" }];

/** A preview of job j1 (`status` and extra fields), with its rows, unmatched chapter and taxonomy records. */
function preview(status = "PREVIEW", extra: Record<string, unknown> = {}, routes: Record<string, unknown> = {}) {
  return stubApi({ ...common, "/api/v1/auth/me": ADMIN, "/api/v1/bulk-imports/j1": job(status, extra),
    "/api/v1/bulk-imports/j1/rows": (url: URL) => page([VALID_ROW, PICTURE_ROW, DUPLICATE_ROW].filter((r) => !url.searchParams.get("status") || r.status === url.searchParams.get("status"))),
    "/api/v1/bulk-imports/j1/taxonomy-issues": [ISSUE], "/api/v1/question-taxonomy/nodes": NODES, ...routes });
}

// ------------------------------------------------------------------ resumable upload: start / complete by fetch, pieces by XHR

/** The server's view of an upload (routes/bulk_uploads.py); pieces of 4 bytes in these tests. */
const SESSION = (over: Record<string, unknown> = {}) => ({ upload_id: "u1", scope: "PROVIDER", provider_id: "p1", subject_id: "phy",
  file_name: "physics.xlsx", size: 2, chunk_size: 4, received_parts: 0, received_bytes: 0, status: "RECEIVING", job_id: null,
  created_at: "2026-11-22T10:00:00Z", updated_at: "2026-11-22T10:00:00Z", resumed: false, ...over });

type Sent = { url: string; size: number };
const sent: Sent[] = [];                                     // the pieces sent (PUT .../parts/{n})
let xhrAnswer: ((url: string) => { status: number; body: unknown }) | null = null;

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
  send(body: Blob) {
    sent.push({ url: this.url, size: body.size });
    const n = Number(/parts\/(\d+)$/.exec(this.url)?.[1] ?? 0);
    const answer = xhrAnswer?.(this.url) ?? { status: 200, body: SESSION({ received_parts: n + 1 }) };
    setTimeout(() => {
      this.upload.onprogress?.({ lengthComputable: true, loaded: body.size / 2, total: body.size });
      this.status = answer.status;
      this.responseText = JSON.stringify(answer.body);
      this.onload?.();
    }, 0);
  }
}

const saved: string[] = [];
beforeEach(() => {
  sent.length = 0;
  saved.length = 0;
  xhrAnswer = null;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  URL.createObjectURL = vi.fn(() => "blob:report");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download); });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const CHEM = { subject_id: "chem", name: "Chemistry" };
const SUPER = me("SUPER_ADMIN", ["paper.read", "question.read", "question.manage", "taxonomy.manage"]);
const TARGETS_PA = { global: null, provider_wide: [{ provider_id: "p1", name: "Alpha", subjects: [{ subject_id: "phy", name: "Physics" }] }], branch: true };
const TARGETS_SA = { global: { subjects: [{ subject_id: "phy", name: "Physics" }, CHEM] }, provider_wide: [], branch: true };

/** Question Bank & Papers > Bulk Upload (the tab), for `user` with the server's import targets. */
function startPage(user = ADMIN, extra: Record<string, unknown> = {}, path = "/papers?tab=bulk") {
  const calls = stubApi({ ...common, "/api/v1/auth/me": user, "/api/v1/bulk-imports/targets": TARGETS_PA,
    "POST /api/v1/bulk-imports/uploads": SESSION(), "POST /api/v1/bulk-imports/uploads/u1/complete": job("UPLOADED"),
    "/api/v1/bulk-imports/uploads": [],
    "/api/v1/bulk-imports": page([job("COMPLETED", { imported_rows: 3, file_name: "earlier-upload.xlsx" })]),
    "/api/v1/bulk-imports/j1": job("PREVIEW"), "/api/v1/bulk-imports/j1/rows": page([VALID_ROW]),
    "/api/v1/bulk-imports/j1/taxonomy-issues": [], ...extra });
  renderApp(path);
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

/** The start of an upload to the provider (the file itself follows in pieces). */
const startedFor = (calls: Call[]) => calls.find((c) => c.method === "POST" && c.path === "/api/v1/bulk-imports/uploads")?.body;
const PROVIDER_START = (name: string) => ({ subject_id: "phy", target: "PROVIDER", provider_id: "p1", file_name: name, size: 2,
  fingerprint: expect.stringMatching(new RegExp(`^${name.replace(".", "\\.")}\\|2\\|`)) });

describe("Bulk Upload tab: upload", () => {
  it("is a tab of Question Bank & Papers, next to Import Processing", async () => {
    startPage();
    const tabs = (await screen.findAllByRole("tab")).map((t) => t.textContent);
    expect(tabs.slice(0, 4)).toEqual(["Question Bank", "Papers", "Import Processing", "Bulk Upload"]);
    expect(await screen.findByLabelText("File (.xlsx or .docx)")).toBeTruthy();
  });

  it("opens from the old address too, with no branch or provider to choose", async () => {
    startPage(ADMIN, {}, "/papers/bulk-import");
    expect(await screen.findByLabelText("File (.xlsx or .docx)")).toBeTruthy();
    expect(screen.queryByLabelText("Upload to")).toBeNull();
    expect(screen.queryByText("No branch available")).toBeNull();
  });

  it.each([["physics.xlsx"], ["physics.docx"]])("uploads %s in the background to the provider, then says it has arrived", async (name) => {
    const calls = startPage();
    await chooseSubject();
    await chooseFile(name);
    expect(screen.getByText(name)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    expect(await screen.findByText(new RegExp(`“${name}” is uploading in the background`))).toBeTruthy();
    expect((screen.getByLabelText("File (.xlsx or .docx)") as HTMLInputElement).value).toBe("");      // free for the next file
    await waitFor(() => expect(startedFor(calls)).toEqual(PROVIDER_START(name)));
    const toast = (await screen.findByText(`Upload finished: ${name}`)).closest(".upload-toast") as HTMLElement;
    expect(toast.getAttribute("role")).toBe("status");                                        // announced to screen readers
    expect(within(toast).getByRole("link", { name: "Open the import" }).getAttribute("href")).toBe("/papers/bulk-import/j1");
    expect(sent).toEqual([{ url: `${API_URL}/api/v1/bulk-imports/uploads/u1/parts/0`, size: 2 }]);
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/bulk-imports/uploads/u1/complete")).toBe(true);
    const tray = screen.getByRole("region", { name: "Uploads" });
    expect(within(tray).getByText("Upload finished")).toBeTruthy();
  });

  it("sends a large file piece by piece and continues an interrupted upload from where it stopped", async () => {
    const calls = startPage(ADMIN, { "POST /api/v1/bulk-imports/uploads": SESSION({ size: 10, received_parts: 1, received_bytes: 4, resumed: true }) });
    await chooseSubject();
    const input = await screen.findByLabelText("File (.xlsx or .docx)");
    fireEvent.change(input, { target: { files: [new File(["PK34567890"], "physics.xlsx")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    expect(await screen.findByText(/Upload finished: physics.xlsx/)).toBeTruthy();
    expect(sent.map((x) => [x.url.split("/").slice(-1)[0], x.size])).toEqual([["1", 4], ["2", 2]]);      // piece 0 had already arrived
    expect(screen.getByText(/continued from 4 KB|continued from/)).toBeTruthy();
    expect(startedFor(calls)).toMatchObject({ size: 10, file_name: "physics.xlsx" });
  });

  it.each([
    ["questions.csv", undefined, "CSV and .doc files are not supported"],
    ["questions.doc", undefined, "CSV and .doc files are not supported"],
    ["huge.xlsx", 2.5 * 1024 * 1024 * 1024, "larger than 2 GB"],
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

  it("shows the server's refusal in the tray, and Continue tries again", async () => {
    xhrAnswer = () => ({ status: 422, body: { detail: { code: "INVALID_FILE", message: "The file is not a valid Excel (.xlsx) or Word (.docx) file." } } });
    startPage();
    await chooseSubject();
    await chooseFile("physics.xlsx");
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    const tray = await screen.findByRole("region", { name: "Uploads" });
    expect(await within(tray).findByText("The file is not a valid Excel (.xlsx) or Word (.docx) file.")).toBeTruthy();
    xhrAnswer = null;
    fireEvent.click(within(tray).getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(/Upload finished: physics.xlsx/)).toBeTruthy();
  });

  it("shows live progress in the upload history while the server imports", async () => {
    startPage(ADMIN, { "/api/v1/bulk-imports": page([job("IMPORTING", { file_name: "big.xlsx", progress: { done: 40, total: 100 } })]) });
    const bar = await screen.findByLabelText("big.xlsx: 40 of 100 imported");
    expect((bar as HTMLProgressElement).value).toBe(40);
    expect(screen.getByText(/40 of 100 imported/)).toBeTruthy();
  });

  it("lists unfinished uploads to continue or discard", async () => {
    const calls = startPage(ADMIN, { "/api/v1/bulk-imports/uploads": [SESSION({ upload_id: "u7", file_name: "big.xlsx", size: 100, received_bytes: 40 })],
      "DELETE /api/v1/bulk-imports/uploads/u7": SESSION({ upload_id: "u7", status: "DISCARDED" }) });
    const card = (await screen.findByRole("heading", { name: "Unfinished uploads" })).closest(".card") as HTMLElement;
    expect(within(card).getByText("big.xlsx")).toBeTruthy();
    expect(within(card).getByText(/40% of/)).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/v1/bulk-imports/uploads/u7")).toBe(true));
  });

  it("asks before signing out while a file is still uploading", async () => {
    xhrAnswer = () => ({ status: 200, body: SESSION({ received_parts: 1 }) });
    let release: () => void = () => {};
    const hold = new Promise<void>((resolve) => { release = resolve; });
    startPage(ADMIN, { "POST /api/v1/bulk-imports/uploads": async () => { await hold; return SESSION(); } });
    await chooseSubject();
    await chooseFile("physics.xlsx");
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("A file is still uploading")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    release();
    expect(await screen.findByText(/Upload finished: physics.xlsx/)).toBeTruthy();
  });

  it("lists the provider's earlier uploads with subject, status and counts", async () => {
    const calls = startPage();
    const link = await screen.findByRole("link", { name: "earlier-upload.xlsx" });
    expect(link.getAttribute("href")).toBe("/papers/bulk-import/j1");
    const row = link.closest("tr")!;
    expect(within(row).getByText("Physics")).toBeTruthy();
    expect(within(row).getByText("COMPLETED")).toBeTruthy();
    expect(within(row).getByText("3 read · 1 ready · 3 imported")).toBeTruthy();
    const asked = calls.find((c) => c.path === "/api/v1/bulk-imports")!;
    expect([asked.search.get("provider_id"), asked.search.get("branch_id")]).toEqual(["p1", null]);
  });

  it("is not offered to staff who may not add questions", async () => {
    startPage(me("TEACHER", ["paper.read", "question.read"]), {
      "/api/v1/bulk-imports/targets": fail(403, { code: "FORBIDDEN", message: "You do not have permission for this action." }) });
    expect(await screen.findByText(/permission/i)).toBeTruthy();
    expect(screen.queryByLabelText("File (.xlsx or .docx)")).toBeNull();
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

  it("pre-selects the likeliest record and lets a taxonomy manager remember the spelling, then checks again", async () => {
    const calls = preview("PREVIEW", {}, { "POST /api/v1/bulk-imports/j1/taxonomy-resolutions": job("VALIDATE_QUEUED"),
      "POST /api/v1/bulk-imports/j1/revalidate": job("VALIDATE_QUEUED") });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/Chapter “Newton Laws”/)).toBeTruthy();
    const select = await screen.findByLabelText("Existing chapter");
    await waitFor(() => expect(within(select).getByRole("option", { name: "Laws of Motion (very likely)" })).toBeTruthy());
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose…", "Laws of Motion (very likely)", "Optics"]);
    expect((select as HTMLSelectElement).value).toBe("n1");                                    // preselected suggestion, not applied yet
    expect(screen.getByText("Did you mean “Laws of Motion”?")).toBeTruthy();
    expect(calls.some((c) => c.path.endsWith("taxonomy-resolutions"))).toBe(false);
    fireEvent.click(screen.getByLabelText("Remember this spelling"));
    expect(screen.getByText("“Newton Laws” will be recognised from now on.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use this chapter" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("taxonomy-resolutions"))?.body)
      .toEqual({ level: "CHAPTER", value: "Newton Laws", parent_id: null, action: "MAP", node_id: "n1", remember: true }));
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/revalidate"))).toBe(true));
  });

  it("lets other staff choose a record and ask for the spelling, but not create records", async () => {
    const calls = preview("PREVIEW", {}, { "/api/v1/auth/me": TEACHER, "/api/v1/bulk-imports/j1/taxonomy-issues": [{ ...ISSUE, may_manage: false }],
      "POST /api/v1/bulk-imports/j1/taxonomy-resolutions": job("VALIDATE_QUEUED") });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/Only an administrator can add new chapters/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Create “Newton Laws”/ })).toBeNull();
    fireEvent.click(await screen.findByLabelText("Remember this spelling"));
    expect(screen.getByText(/An administrator will be asked to approve this spelling/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use this chapter" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("taxonomy-resolutions"))?.body)
      .toEqual({ level: "CHAPTER", value: "Newton Laws", parent_id: null, action: "MAP", node_id: "n1", remember: true }));
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

// ------------------------------------------------------------------ where the questions go: the provider, or the platform

describe("Bulk Upload tab: where the questions go", () => {
  it("a teacher or Branch Admin uploads to the provider without choosing anything", async () => {
    const calls = startPage(TEACHER);
    expect(await screen.findByLabelText("File (.xlsx or .docx)")).toBeTruthy();
    expect(screen.queryByLabelText("Upload to")).toBeNull();
    expect(screen.getByText(/Alpha's Question Bank, shared with all its branches/)).toBeTruthy();
    await chooseSubject();
    await chooseFile("physics.xlsx");
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    await waitFor(() => expect(startedFor(calls)).toEqual(PROVIDER_START("physics.xlsx")));
  });

  it("a platform administrator uploads into the platform bank with every platform subject", async () => {
    const calls = startPage(SUPER, { "/api/v1/bulk-imports/targets": TARGETS_SA });
    const select = await screen.findByLabelText("Subject");
    await waitFor(() => expect(within(select).getByRole("option", { name: "Chemistry" })).toBeTruthy());
    fireEvent.change(select, { target: { value: "chem" } });
    await chooseFile("chemistry.docx");
    fireEvent.click(screen.getByRole("button", { name: "Upload and check" }));
    await waitFor(() => expect(startedFor(calls)).toMatchObject({ target: "GLOBAL", provider_id: null, subject_id: "chem", file_name: "chemistry.docx" }));
    expect(calls.some((c) => c.path === "/api/v1/bulk-imports" && c.search.get("target") === "GLOBAL")).toBe(true);
  });

  it("offers a choice only when there is more than one place to upload to", async () => {
    startPage(SUPER, { "/api/v1/bulk-imports/targets": { ...TARGETS_SA, provider_wide: TARGETS_PA.provider_wide } });
    const select = await screen.findByLabelText("Upload to");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Platform question bank (all institutes)", "Alpha"]);
    fireEvent.change(select, { target: { value: "p1" } });
    expect(await screen.findByText(/Alpha's Question Bank/)).toBeTruthy();
  });

  it("resolves chapters of a platform import against the platform list", async () => {
    const calls = preview("PREVIEW", { scope: "GLOBAL", provider_id: null, branch_id: null });
    renderApp("/papers/bulk-import/j1");
    expect(await screen.findByText(/into the platform question bank/)).toBeTruthy();
    await screen.findByLabelText("Existing chapter");
    const nodes = calls.find((c) => c.path.startsWith("/api/v1/question-taxonomy/nodes"));
    expect(nodes?.search.get("subject_id")).toBeTruthy();
    expect(nodes?.search.has("provider_id")).toBe(false);                                     // the platform list
  });
});
