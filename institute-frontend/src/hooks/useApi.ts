import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../services/api";

type Result<T> = { key: string; data: T | null; error: string };

/** GET helper with loading/error/reload. `path === null` skips the request. State is only set from
 * the async callbacks, and `loading` is derived, so a stale response can never overwrite a newer one. */
export function useApi<T>(path: string | null) {
  const [result, setResult] = useState<Result<T> | null>(null);
  const [tick, setTick] = useState(0);
  const key = path === null ? "" : `${path}#${tick}`;

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    api<T>(path)
      .then((data) => { if (!cancelled) setResult({ key, data, error: "" }); })
      .catch((e) => { if (!cancelled) setResult({ key, data: null, error: errorMessage(e) }); });
    return () => { cancelled = true; };
  }, [path, key]);

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
