import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "./renderApp";
import { me, page, stubApi } from "./stubApi";

const PERMS = ["paper.read", "question.read", "paper.create", "paper.approve", "paper.publish"];
const version = { paper_version_id: "pv1", paper_id: "pp", version_no: 1, status: "PUBLISHED", question_count: 1, total_marks: "4.00", created_at: "2026-10-01T00:00:00Z" };
const detail = { paper_id: "pp", scope: "PROVIDER", subject_id: "phy", title: "Daily paper", latest_version: version, history: [],
  questions: [{ question_version_id: "qv", version_no: 1, stem: "What is g?", option_a: "9.8", option_b: "1", option_c: "2", option_d: "3", correct_option: "A",
    marks_positive: "4", marks_negative: "1", explanation: null, topic: null, answers_hidden: false }] };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/paper-versions/pv1": detail, "/api/v1/papers/pp/versions": [version],
  "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]),
  "/api/v1/subjects": page([{ subject_id: "phy", code: "PHYSICS", name: "Physics", status: "ACTIVE" }]),
  "/api/v1/papers": page([{ paper_id: "pp", scope: "PROVIDER", provider_id: "p1", branch_id: "b1", subject_id: "phy", title: "Daily paper", created_by: "s", latest_version: version }]) };

const saved: string[] = [];
const fetched: URL[] = [];
const FILES: Record<string, string> = { "/api/v1/paper-versions/pv1/pdf": "Daily-paper-v1.pdf", "/api/v1/paper-versions/pv1/answer-key": "Daily-paper-v1-answer-key.pdf",
  "/api/v1/papers/pdf": "question-papers.zip", "/api/v1/papers/answer-keys": "answer-keys.zip" };
/** stubApi for the JSON calls; a download answers with a file (the server's name in Content-Disposition), or `refuse` with an API error. */
function stubWithFiles(routes: Record<string, unknown>, refuse?: { status: number; detail: unknown }) {
  stubApi(routes);
  const json = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input);
    if (!(url.pathname in FILES)) return json(input, init);
    fetched.push(url);
    if (refuse) return { ok: false, status: refuse.status, json: async () => ({ detail: refuse.detail }) } as Response;
    return { ok: true, status: 200, headers: new Headers({ "Content-Disposition": `attachment; filename="${FILES[url.pathname]}"` }),
      blob: async () => new Blob(["%PDF"]) } as Response;
  }));
}

beforeEach(() => {
  saved.length = 0;
  fetched.length = 0;
  URL.createObjectURL = vi.fn(() => "blob:paper");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download); });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Paper PDF downloads", () => {
  it("downloads the question paper and, separately, the answer key", async () => {
    stubWithFiles({ ...common, "/api/v1/auth/me": me("TEACHER", PERMS) });
    renderApp("/papers/pv1");
    fireEvent.click(await screen.findByRole("button", { name: "Download Question Paper" }));
    await waitFor(() => expect(saved).toEqual(["Daily-paper-v1.pdf"]));
    fireEvent.click(screen.getByRole("button", { name: "Download Answer Key" }));
    await waitFor(() => expect(saved).toEqual(["Daily-paper-v1.pdf", "Daily-paper-v1-answer-key.pdf"]));
    expect(fetched.map((u) => u.pathname)).toEqual(["/api/v1/paper-versions/pv1/pdf", "/api/v1/paper-versions/pv1/answer-key"]);
  });

  it("does not offer the answer key when the paper's answers are withheld from the viewer", async () => {
    const hidden = { ...detail, questions: detail.questions.map((q) => ({ ...q, correct_option: null, answers_hidden: true })) };
    stubWithFiles({ ...common, "/api/v1/paper-versions/pv1": hidden, "/api/v1/auth/me": me("PROVIDER_ADMIN", PERMS) });
    renderApp("/papers/pv1");
    expect(await screen.findByRole("button", { name: "Download Question Paper" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Download Answer Key" })).toBeNull();
  });

  it("shows the server's refusal instead of a file", async () => {
    stubWithFiles({ ...common, "/api/v1/auth/me": me("TEACHER", PERMS) }, { status: 403, detail: { code: "FORBIDDEN", message: "The answer key is not available to you for this paper." } });
    renderApp("/papers/pv1");
    fireEvent.click(await screen.findByRole("button", { name: "Download Answer Key" }));
    expect(await screen.findByText("The answer key is not available to you for this paper.")).toBeTruthy();
    expect(saved).toEqual([]);
  });

  it("downloads question papers and answer keys as ZIPs with the list's filters, or from all branches for a Provider Admin", async () => {
    stubWithFiles({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", PERMS) });
    renderApp("/papers?tab=papers");
    expect(await screen.findByRole("link", { name: "Daily paper" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Has a version that is"), { target: { value: "PUBLISHED" } });
    fireEvent.click(await screen.findByRole("button", { name: "Download Question Papers (ZIP)" }));
    await waitFor(() => expect(saved).toEqual(["question-papers.zip"]));
    fireEvent.click(screen.getByRole("button", { name: "Download Answer Keys (ZIP)" }));
    await waitFor(() => expect(saved).toEqual(["question-papers.zip", "answer-keys.zip"]));
    const branch = { scope: "PROVIDER", branch_id: "b1", status: "PUBLISHED" };                    // every page, the same filters
    expect(fetched.map((u) => [u.pathname, Object.fromEntries(u.searchParams)])).toEqual([["/api/v1/papers/pdf", branch], ["/api/v1/papers/answer-keys", branch]]);
    fireEvent.change(screen.getByLabelText("Download from"), { target: { value: "ALL" } });
    fireEvent.click(screen.getByRole("button", { name: "Download Answer Keys (ZIP)" }));
    await waitFor(() => expect(saved).toHaveLength(3));
    expect(Object.fromEntries(fetched[2].searchParams)).toEqual({ scope: "PROVIDER", provider_id: "p1", status: "PUBLISHED" });
  });

  it("offers a Branch Admin only their branch's papers", async () => {
    stubWithFiles({ ...common, "/api/v1/auth/me": me("BRANCH_ADMIN", PERMS) });
    renderApp("/papers?tab=papers");
    expect(await screen.findByRole("button", { name: "Download Question Papers (ZIP)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Download Answer Keys (ZIP)" })).toBeTruthy();
    expect(screen.queryByLabelText("Download from")).toBeNull();
  });

  it("explains a bulk download the server bounds", async () => {
    stubWithFiles({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", PERMS) },
      { status: 422, detail: { code: "TOO_MANY_PAPERS", message: "40 papers match; download at most 30 at once (choose a branch or a subject)." } });
    renderApp("/papers?tab=papers");
    expect(await screen.findByRole("link", { name: "Daily paper" })).toBeTruthy();          // the list has loaded: the button is enabled
    fireEvent.click(screen.getByRole("button", { name: "Download Question Papers (ZIP)" }));
    expect(await screen.findByText(/download at most 30 at once/)).toBeTruthy();
  });
});
