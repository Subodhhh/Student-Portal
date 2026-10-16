export const API_URL: string = import.meta.env.VITE_INSTITUTE_API_URL ?? "http://localhost:8010";

export class ApiError extends Error {
  status: number;
  code: string;
  extra: Record<string, unknown>;
  constructor(status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

let csrfToken: string | null = null;
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export const SESSION_EXPIRED_EVENT = "institute:session-expired";

type Options = { method?: string; body?: unknown; form?: FormData; headers?: Record<string, string> };

function messageFrom(detail: unknown): { code: string; message: string; extra: Record<string, unknown> } {
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as Record<string, unknown>;
    return { code: String(d.code ?? "ERROR"), message: String(d.message ?? "Request failed."), extra: d };
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "input";
    return { code: "VALIDATION", message: `${field}: ${first.msg ?? "is invalid"}`, extra: {} };
  }
  return { code: "ERROR", message: typeof detail === "string" ? detail : "Request failed.", extra: {} };
}

/** All calls send the session cookie and, for state-changing requests, the session-bound CSRF token. */
export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      credentials: "include",
      headers,
      body: opts.form ?? (opts.body === undefined ? undefined : JSON.stringify(opts.body)),
    });
  } catch {
    throw new ApiError(0, "NETWORK", "Cannot reach the server. Check your connection and try again.");
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const { code, message, extra } = messageFrom(data?.detail);
    if (response.status === 401 && !path.startsWith("/api/v1/auth/")) {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    throw new ApiError(response.status, code, message, extra);
  }
  return data as T;
}

export const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : "Something went wrong.");

export type Page<T> = { items: T[]; total: number; page: number; page_size: number };
