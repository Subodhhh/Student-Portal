import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, SESSION_EXPIRED_EVENT, api, setCsrfToken } from "./api";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("api client", () => {
  it("sends credentials and the CSRF token only on state-changing requests", async () => {
    const fn = mockFetch(200, { ok: true });
    setCsrfToken("tok");
    await api("/api/v1/x");
    await api("/api/v1/x", { method: "POST", body: { a: 1 } });
    expect(fn.mock.calls[0][1].credentials).toBe("include");
    expect(fn.mock.calls[0][1].headers["X-CSRF-Token"]).toBeUndefined();
    expect(fn.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("tok");
  });

  it("turns API errors into readable ApiError values", async () => {
    mockFetch(403, { detail: { code: "FORBIDDEN", message: "Nope." } });
    await expect(api("/api/v1/x")).rejects.toMatchObject({ status: 403, code: "FORBIDDEN", message: "Nope." });
  });

  it("formats validation errors", async () => {
    mockFetch(422, { detail: [{ loc: ["body", "email"], msg: "value is not a valid email address" }] });
    await expect(api("/api/v1/x", { method: "POST", body: {} })).rejects.toMatchObject({ code: "VALIDATION", message: expect.stringContaining("email") });
  });

  it("announces an expired session for protected calls but not for auth calls", async () => {
    const seen = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, seen);
    mockFetch(401, { detail: { code: "NOT_AUTHENTICATED", message: "Not authenticated." } });
    await expect(api("/api/v1/providers")).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalledTimes(1);
    await expect(api("/api/v1/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalledTimes(1);
    window.removeEventListener(SESSION_EXPIRED_EVENT, seen);
  });

  it("reports network failures clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fail")));
    await expect(api("/api/v1/x")).rejects.toMatchObject({ code: "NETWORK" });
  });
});
