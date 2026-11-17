import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { FRESH_MS, MAX_AGE_MS, clearReference, clearScopeHints, loadReference, peekReference, scopeHintKey } from "./referenceCache";
import { fail, stubApi } from "../test/stubApi";

afterEach(() => { clearReference(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("reference cache", () => {
  it("shares one request between simultaneous readers and keeps the answer", async () => {
    const fetcher = vi.fn(async () => ({ items: [1] }));
    const [a, b] = await Promise.all([loadReference("/x", fetcher), loadReference("/x", fetcher)]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(a).toEqual(b);
    expect(peekReference("/x")).toEqual({ data: { items: [1] }, fresh: true });
  });

  it("is fresh for 30 s, usable (to refresh in the background) up to 5 min, then gone", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-10T10:00:00Z"));
    await loadReference("/x", async () => "v1");
    vi.setSystemTime(Date.now() + FRESH_MS + 1);
    expect(peekReference("/x")).toEqual({ data: "v1", fresh: false });
    vi.setSystemTime(Date.now() + MAX_AGE_MS);
    expect(peekReference("/x")).toBeUndefined();
  });

  it("never keeps a failed answer, and a forced load always asks again", async () => {
    await expect(loadReference("/x", async () => { throw new Error("down"); })).rejects.toThrow("down");
    expect(peekReference("/x")).toBeUndefined();
    const fetcher = vi.fn(async () => "v");
    await loadReference("/x", fetcher);
    await loadReference("/x", fetcher, true);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("an answer that arrives after a clear (sign-out, a change) is never stored", async () => {
    let release: (v: string) => void = () => {};
    const pending = loadReference("/x", () => new Promise<string>((r) => { release = r; }));
    clearReference();
    release("previous user's list");
    await pending;
    expect(peekReference("/x")).toBeUndefined();
  });

  it("is dropped after every successful change through the API, but not after a read or a refused change", async () => {
    stubApi({ "/api/v1/subjects": { items: [] }, "POST /api/v1/subjects": { subject_id: "s1" },
      "PATCH /api/v1/subjects/s1": fail(422, { code: "INVALID", message: "no" }) });
    await loadReference("/k", async () => "v");
    await api("/api/v1/subjects");
    expect(peekReference("/k")).toBeDefined();
    await expect(api("/api/v1/subjects/s1", { method: "PATCH", body: {} })).rejects.toThrow();
    expect(peekReference("/k")).toBeDefined();
    await api("/api/v1/subjects", { method: "POST", body: { code: "X", name: "X" } });
    expect(peekReference("/k")).toBeUndefined();
  });

  it("sign-out forgets the start-up hints (only provider ids) of every user on this device", () => {
    localStorage.setItem(scopeHintKey("a"), "p1");
    localStorage.setItem(scopeHintKey("b"), "p2");
    localStorage.setItem("institute.other", "kept");
    clearScopeHints();
    expect(localStorage.getItem(scopeHintKey("a"))).toBeNull();
    expect(localStorage.getItem(scopeHintKey("b"))).toBeNull();
    expect(localStorage.getItem("institute.other")).toBe("kept");
  });
});
