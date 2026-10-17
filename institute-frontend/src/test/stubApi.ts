import { vi } from "vitest";

type Handler = unknown | ((url: URL, init: RequestInit) => unknown);
export type Call = { method: string; path: string; search: URLSearchParams; body: unknown };

/** Stubs fetch with responses keyed by "METHOD /path" or "/path" (GET). Unknown routes answer 404. Returns the calls made. */
export function stubApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = new URL(input);
    const method = (init.method ?? "GET").toUpperCase();
    // JSON bodies are parsed; multipart uploads are recorded as { field: value or file name }.
    const body = typeof init.body === "string" ? JSON.parse(init.body)
      : init.body instanceof FormData ? Object.fromEntries([...init.body.entries()].map(([k, v]) => [k, typeof v === "string" ? v : (v as File).name])) : undefined;
    calls.push({ method, path: url.pathname, search: url.searchParams, body });
    const handler = routes[`${method} ${url.pathname}`] ?? (method === "GET" ? routes[url.pathname] : undefined);
    const data = typeof handler === "function" ? (handler as (u: URL, i: RequestInit) => unknown)(url, init) : handler;
    if (data instanceof Failure) return { ok: false, status: data.status, json: async () => data.body } as Response;
    const ok = data !== undefined;
    return { ok, status: ok ? 200 : 404, json: async () => (ok ? data : { detail: { code: "NOT_FOUND", message: `unhandled ${method} ${url.pathname}` } }) } as Response;
  }));
  return calls;
}

/** Return `fail(422, {...})` from a handler to answer with that status and body. */
export class Failure {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    this.status = status;
    this.body = body;
  }
}
export const fail = (status: number, detail: unknown) => new Failure(status, { detail });

export const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 20 });

export const me = (role: string, permissions: string[], extra: Record<string, unknown> = {}) => ({
  staff_id: "me", staff_code: "STF000001", full_name: "Sam Admin", email: "sam@example.com", primary_role: role,
  assignments: [], permissions, csrf_token: "csrf", ...extra,
});
