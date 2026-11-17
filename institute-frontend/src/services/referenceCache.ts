/** In-memory cache of shared REFERENCE lists (subject catalogue, provider subjects, the provider and branch lists) for this tab.
 *
 * Only for data that many pages read and that rarely changes. Never for permissions, the session, students, results or other
 * user-specific records. Everything is dropped on sign-in, sign-out, an expired session and after every successful change made
 * through the API (create, edit, (de)activate...), so a list is never reused across users nor after an edit. The backend still
 * authorizes and validates every request; a cached list is only what to show and offer.
 */
export const FRESH_MS = 30_000;          // reused as is
export const MAX_AGE_MS = 5 * 60_000;    // reused while it is refreshed in the background; older entries are not used at all

type Entry = { data: unknown; at: number };
const store = new Map<string, Entry>();
const inflight = new Map<string, Promise<unknown>>();
let generation = 0;                       // bumped by clearReference: an answer requested before a clear never fills the cache

export function clearReference(): void {
  store.clear();
  inflight.clear();
  generation += 1;
}

/** The cached value of `path` if it is still usable, and whether it is fresh enough to skip asking the server. */
export function peekReference<T>(path: string): { data: T; fresh: boolean } | undefined {
  const e = store.get(path);
  if (!e) return undefined;
  const age = Date.now() - e.at;
  if (age > MAX_AGE_MS) {
    store.delete(path);
    return undefined;
  }
  return { data: e.data as T, fresh: age < FRESH_MS };
}

/** Loads `path` once even when several pages ask at the same time (they share the request); `force` always asks the server. */
export function loadReference<T>(path: string, fetcher: (path: string) => Promise<T>, force = false): Promise<T> {
  const pending = force ? undefined : inflight.get(path);
  if (pending) return pending as Promise<T>;
  const gen = generation;
  const request: Promise<T> = fetcher(path)
    .then((data) => {
      if (gen === generation) store.set(path, { data, at: Date.now() });
      return data;
    })
    .finally(() => {
      if (inflight.get(path) === request) inflight.delete(path);
    });
  inflight.set(path, request);
  return request;
}

/** localStorage key of a user's start-up hint (auth/ScopeContext): only the id of the provider that was first in their list. */
export const scopeHintKey = (staffId: string) => `institute.scope.first.${staffId}`;

/** Sign-out forgets every user's start-up hint on this device. */
export function clearScopeHints(): void {
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith("institute.scope.first.")) localStorage.removeItem(k);
  } catch { /* storage may be unavailable */ }
}
