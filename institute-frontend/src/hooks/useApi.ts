import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiAllPages, errorMessage } from "../services/api";
import { loadReference, peekReference } from "../services/referenceCache";

type Result<T> = { key: string; data: T | null; error: string };

/** GET helper with loading/error/reload. `path === null` skips the request. State is only set from
 * the async callbacks, and `loading` is derived, so a stale response can never overwrite a newer one. `allPages`: a paged list is
 * fetched page after page and returned whole (services/api.ts apiAllPages). */
export function useApi<T>(path: string | null, allPages = false) {
  const [result, setResult] = useState<Result<T> | null>(null);
  const [tick, setTick] = useState(0);
  const key = path === null ? "" : `${path}#${tick}`;

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    (allPages ? (apiAllPages(path) as Promise<T>) : api<T>(path))
      .then((data) => { if (!cancelled) setResult({ key, data, error: "" }); })
      .catch((e) => { if (!cancelled) setResult({ key, data: null, error: errorMessage(e) }); });
    return () => { cancelled = true; };
  }, [path, key, allPages]);

  const fresh = path !== null && result?.key === key;
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return {
    // keep showing the previous data while a reload is in flight (no flicker)
    data: path === null ? null : (result?.data ?? null),
    error: fresh ? result.error : "",
    loading: path !== null && !fresh,
    reload,
  };
}

/** useApi for shared REFERENCE lists only (services/referenceCache.ts: subject catalogue, provider subjects, provider and branch
 * lists): a list another page loaded in the last 30 s is shown at once without a request; one up to 5 minutes old is shown while
 * it is refreshed in the background; pages asking at the same time share one request. `reload()` always asks the server. */
export function useReference<T>(path: string | null, allPages = false) {
  const [result, setResult] = useState<Result<T> | null>(null);
  const [tick, setTick] = useState(0);
  const key = path === null ? "" : `${path}#${tick}`;
  const hit = path === null ? undefined : peekReference<T>(path);

  useEffect(() => {
    if (path === null) return;
    if (tick === 0 && peekReference(path)?.fresh) return;          // reused as is
    let cancelled = false;
    loadReference<T>(path, (p) => (allPages ? (apiAllPages(p) as Promise<T>) : api<T>(p)), tick > 0)
      .then((data) => { if (!cancelled) setResult({ key, data, error: "" }); })
      .catch((e) => { if (!cancelled) setResult({ key, data: null, error: errorMessage(e) }); });
    return () => { cancelled = true; };
  }, [path, key, tick, allPages]);

  const fresh = path !== null && result?.key === key;
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return {
    data: path === null ? null : ((fresh ? result.data : null) ?? hit?.data ?? result?.data ?? null),
    error: fresh ? result.error : "",
    loading: path !== null && !fresh && !hit,
    reload,
  };
}

/** Runs a mutation once at a time (prevents double submits) and surfaces the error text. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = useRef(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    if (running.current) return undefined;
    running.current = true;
    setBusy(true);
    setError("");
    try { return await fn(); } catch (e) { setError(errorMessage(e)); return undefined; } finally { running.current = false; setBusy(false); }
  }, []);
  return { busy, error, run, setError };
}
