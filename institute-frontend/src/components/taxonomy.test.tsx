import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const PROVIDER = { provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" };
const PHY = { subject_id: "phy", code: "PHYSICS", name: "Physics", status: "ACTIVE" };
const common = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([PROVIDER]),
  "/api/v1/providers/p1/branches": page([]), "/api/v1/subjects": page([PHY]), "/api/v1/providers/p1/subjects": [PHY] };
const node = (id: string, name: string, level: string, extra: Record<string, unknown> = {}) => ({ node_id: id, provider_id: "p1", subject_id: "phy",
  parent_id: null, level, name, status: "ACTIVE", aliases: [], questions: 0, children: [], ...extra });
const TREE = [node("c1", "Laws of Motion", "CHAPTER", { questions: 3, aliases: [{ alias_id: "a1", alias: "Newton's Laws" }],
  children: [node("t1", "Force", "TOPIC", { parent_id: "c1" })] }), node("c2", "Newtons Laws", "CHAPTER")];
const PA = me("PROVIDER_ADMIN", ["question.read", "question.manage", "taxonomy.manage", "paper.read"]);
afterEach(() => vi.unstubAllGlobals());

describe("Chapters & Topics", () => {
  it("shows the tree with spellings and counts, merges a duplicate and decides a spelling request", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": PA, "/api/v1/question-taxonomy/tree": TREE,
      "/api/v1/question-taxonomy/alias-requests": [{ request_id: "r1", node_id: "c1", node_name: "Laws of Motion", level: "CHAPTER",
        alias: "Newtons1st Law", source: "BULK_UPLOAD", status: "PENDING", requested_by: "s1", requested_at: "2026-11-22T00:00:00Z", decision_reason: null }],
      "/api/v1/question-taxonomy/unlinked": [{ level: "CHAPTER", value: "Law of Motions", parent_id: null, questions: 2, preselect: "c1",
        suggestions: [{ node_id: "c1", name: "Laws of Motion", band: "HIGH" }], elsewhere: [] }],
      "POST /api/v1/question-taxonomy/nodes/c2/merge": TREE[0], "POST /api/v1/question-taxonomy/alias-requests/r1/approve": {},
      "POST /api/v1/question-taxonomy/unlinked/resolve": { questions: 2, remembered: true } });
    renderApp("/papers?tab=chapters");
    expect(await screen.findByText("Laws of Motion")).toBeTruthy();
    expect(screen.getByText("Newton's Laws")).toBeTruthy();
    expect(screen.getByText(/chapter · 3 question\(s\)/)).toBeTruthy();
    const tree = calls.find((c) => c.path === "/api/v1/question-taxonomy/tree")!.search;
    expect([tree.get("provider_id"), tree.get("subject_id")]).toEqual(["p1", "phy"]);

    fireEvent.click(screen.getAllByRole("button", { name: "Merge into…" })[2]);              // "Newtons Laws" (after Laws of Motion, Force)
    const d = await screen.findByRole("dialog", { name: "Merge “Newtons Laws” into…" });
    fireEvent.change(within(d).getByLabelText("Keep"), { target: { value: "c1" } });
    fireEvent.click(within(d).getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/merge"))?.body).toEqual({ into_id: "c1" }));

    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/r1/approve"))).toBe(true));

    const official = screen.getByLabelText("Official chapter") as HTMLSelectElement;
    expect(official.value).toBe("c1");                                                      // pre-selected, not applied
    expect(calls.some((c) => c.path.endsWith("/unlinked/resolve"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Link 2 question(s)" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/unlinked/resolve"))?.body).toEqual({ provider_id: "p1", subject_id: "phy",
      level: "CHAPTER", parent_id: null, value: "Law of Motions", node_id: "c1", remember: true }));
  });

  it("is read-only for staff who do not manage the list", async () => {
    const calls = stubApi({ ...common, "/api/v1/auth/me": me("TEACHER", ["question.read", "question.manage", "paper.read"]), "/api/v1/question-taxonomy/tree": TREE,
      "/api/v1/question-taxonomy/alias-requests": [] });
    renderApp("/papers?tab=chapters");
    expect(await screen.findByText("Laws of Motion")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add chapter" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rename" })).toBeNull();
    expect(calls.some((c) => c.path.endsWith("/unlinked"))).toBe(false);
  });
});
