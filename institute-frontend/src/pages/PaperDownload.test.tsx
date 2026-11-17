import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

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
const FILES: Record<string, string> = { "/api/v1/paper-versions/pv1/pdf": "Daily-paper-v1.pdf", "/api/v1/paper-versions/pv1/answer-key": "Daily-paper-v1-answer-key.pdf" };
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

  it.each(["VALIDATED", "WITHDRAWN"])("offers no download for a %s version", async (status) => {
    const draft = { ...detail, latest_version: { ...version, status } };
    stubWithFiles({ ...common, "/api/v1/paper-versions/pv1": draft, "/api/v1/auth/me": me("PROVIDER_ADMIN", PERMS) });
    renderApp("/papers/pv1");
    expect(await screen.findByText("Questions (preview — visible to authorized staff only)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Download Question Paper" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Download Answer Key" })).toBeNull();
  });

  it("has no ZIP downloads on the Papers list", async () => {
    stubWithFiles({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", PERMS) });
    renderApp("/papers?tab=papers");
    expect(await screen.findByRole("link", { name: "Daily paper" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /ZIP/ })).toBeNull();
    expect(screen.queryByLabelText("Download from")).toBeNull();
  });

  it("lists every accessible branch with All Branches, by provider rather than by branch", async () => {
    const seen: string[] = [];
    stubWithFiles({ ...common, "/api/v1/auth/me": me("PROVIDER_ADMIN", PERMS),
      "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }, { branch_id: "b2", provider_id: "p1", name: "North", city: null, status: "ACTIVE" }]) });
    const json = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => { if (new URL(input).pathname === "/api/v1/papers") seen.push(new URL(input).search); return json(input, init); }));
    renderApp("/papers?tab=papers");
    expect(await screen.findByRole("link", { name: "Daily paper" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Branch"), { target: { value: "__all__" } });
    expect(await screen.findByRole("columnheader", { name: "Branch" })).toBeTruthy();
    expect(seen.at(-1)).toContain("provider_id=p1");
    expect(seen.at(-1)).not.toContain("branch_id");
    expect(await screen.findByRole("cell", { name: "Main" })).toBeTruthy();
  });
});
