import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi, type Call } from "../test/stubApi";
import { api } from "../services/api";
import { clearReference, loadReference, peekReference, scopeHintKey } from "../services/referenceCache";

const P1 = { provider_id: "p1", name: "Alpha", status: "ACTIVE", email: "a@x.io" };
const P2 = { provider_id: "p2", name: "Beta", status: "ACTIVE", email: "b@x.io" };
const CATALOGUE = page([{ subject_id: "phy", name: "Physics", code: "PHY", status: "ACTIVE" }]);
const base = { "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/providers": page([P1, P2]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]),
  "/api/v1/subjects": CATALOGUE, "/api/v1/question-taxonomy": { subjects: [] }, "/api/v1/questions": page([]), "/api/v1/branches/b1/exams": page([]) };
const count = (calls: Call[], path: string, search?: [string, string]) =>
  calls.filter((c) => c.method === "GET" && c.path === path && (!search || c.search.get(search[0]) === search[1])).length;
const nav = async (to: string) => fireEvent.click(await waitFor(() => {
  const link = document.querySelector(`aside a[href="${to}"]`);
  if (!link) throw new Error(`no link to ${to}`);
  return link;
}));
afterEach(() => { vi.unstubAllGlobals(); clearReference(); localStorage.clear(); sessionStorage.clear(); });

describe("shared reference lists", () => {
  it("are reused across page navigation: the catalogue, providers and branches are each read once", async () => {
    const calls = stubApi({ ...base, "/api/v1/auth/me": me("SUPER_ADMIN", ["paper.read", "exam.read"]) });
    renderApp("/papers");
    await waitFor(() => expect(count(calls, "/api/v1/subjects", ["page_size", "100"])).toBe(1));
    await nav("/exams");
    await waitFor(() => expect(count(calls, "/api/v1/branches/b1/exams")).toBe(1));
    await nav("/papers");
    await waitFor(() => expect(count(calls, "/api/v1/questions")).toBe(2));          // page data: always asked again
    expect(count(calls, "/api/v1/subjects", ["page_size", "100"])).toBe(1);           // reference: reused
    expect(count(calls, "/api/v1/providers")).toBe(1);
    expect(count(calls, "/api/v1/providers/p1/branches")).toBe(1);
  });

  it("are read again after any change made through the API (create, edit, (de)activate)", async () => {
    const calls = stubApi({ ...base, "/api/v1/auth/me": me("SUPER_ADMIN", ["paper.read", "exam.read"]), "POST /api/v1/subjects": { subject_id: "chem" } });
    renderApp("/papers");
    await waitFor(() => expect(count(calls, "/api/v1/subjects", ["page_size", "100"])).toBe(1));
    await nav("/exams");
    await api("/api/v1/subjects", { method: "POST", body: { code: "CHE", name: "Chemistry" } });
    await nav("/papers");
    await waitFor(() => expect(count(calls, "/api/v1/subjects", ["page_size", "100"])).toBe(2));
  });

  it("are never carried over to another session: signing in starts with an empty cache", async () => {
    await loadReference("/api/v1/subjects?page_size=100", async () => page([{ subject_id: "old", name: "Previous user's subject" }]));
    const calls = stubApi({ ...base, "/api/v1/auth/me": me("SUPER_ADMIN", ["paper.read", "exam.read"]) });
    renderApp("/papers");
    await waitFor(() => expect(count(calls, "/api/v1/subjects", ["page_size", "100"])).toBe(1));
    expect(screen.queryByText("Previous user's subject")).toBeNull();
    expect(peekReference<{ items: { subject_id: string }[] }>("/api/v1/subjects?page_size=100")?.data.items[0].subject_id).toBe("phy");
  });
});

describe("start-up", () => {
  it("asks a platform user's branch list together with the provider list when the device remembers which provider came first", async () => {
    localStorage.setItem(scopeHintKey("me"), "p1");
    let release: (v: unknown) => void = () => {};
    const providers = new Promise((r) => { release = r; });
    const calls = stubApi({ ...base, "/api/v1/auth/me": me("SUPER_ADMIN", ["paper.read", "exam.read"]), "/api/v1/providers": () => providers });
    renderApp("/exams");
    await waitFor(() => expect(count(calls, "/api/v1/providers/p1/branches")).toBe(1));   // before the provider list answered
    release(page([P1, P2]));
    await waitFor(() => expect(count(calls, "/api/v1/branches/b1/exams")).toBe(1));
    expect(count(calls, "/api/v1/providers/p1/branches")).toBe(1);                           // the hinted request was the one used
  });

  it("without a hint keeps the previous order, and remembers only the first provider's id for next time", async () => {
    const calls = stubApi({ ...base, "/api/v1/auth/me": me("SUPER_ADMIN", ["paper.read", "exam.read"]) });
    renderApp("/exams");
    await waitFor(() => expect(count(calls, "/api/v1/branches/b1/exams")).toBe(1));
    const providersAt = calls.findIndex((c) => c.path === "/api/v1/providers");
    const branchesAt = calls.findIndex((c) => c.path === "/api/v1/providers/p1/branches");
    expect(branchesAt).toBeGreaterThan(providersAt);
    expect(localStorage.getItem(scopeHintKey("me"))).toBe("p1");
  });

  it("a stale hint (that provider is no longer first) costs one request and never changes the selection", async () => {
    localStorage.setItem(scopeHintKey("me"), "p2");
    const calls = stubApi({ ...base, "/api/v1/auth/me": me("SUPER_ADMIN", ["paper.read", "exam.read"]),
      "/api/v1/providers/p2/branches": page([{ branch_id: "b2", provider_id: "p2", name: "Other", city: null, status: "ACTIVE" }]) });
    renderApp("/exams");
    await waitFor(() => expect(count(calls, "/api/v1/branches/b1/exams")).toBe(1));            // the first provider's branch, as before
    expect(count(calls, "/api/v1/branches/b2/exams")).toBe(0);
    expect(localStorage.getItem(scopeHintKey("me"))).toBe("p1");
  });
});
