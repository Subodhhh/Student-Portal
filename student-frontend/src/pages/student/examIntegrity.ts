import { useCallback, useEffect, useRef, useState } from "react";

// Exam integrity in the browser: report leaving the exam (tab hidden, window blur, fullscreen exit) to the attempt service, which
// counts warnings ON THE SERVER (de-duplicated, idempotent) and submits the attempt automatically at its threshold. The browser cannot
// prevent leaving; it only reports. Displayed counts are always the server's.

export type IntegrityState = {
  attempt_id: string;
  status: string;
  warnings: number;
  threshold: number;
  submit_reason: string | null;
  review_status: string | null;
  review: { status: string; student_message: string; decision_reason: string | null } | null;
  auto_submitted?: boolean;
};

type Kind = "TAB_HIDDEN" | "WINDOW_BLUR" | "FULLSCREEN_EXIT";

const RETRIES = [500, 1500, 4000];

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
    (Number(c) ^ (Math.random() * 16) >> (Number(c) / 4)).toString(16));
}

export function useExamIntegrity(apiUrl: string, attemptId: string | null, active: boolean,
                                 onAutoSubmitted: (s: IntegrityState) => void) {
  const [state, setState] = useState<IntegrityState | null>(null);
  const suppressUntil = useRef(0);
  const inFullscreen = useRef(false);
  const done = useRef(false);
  const unloading = useRef(false);
  const callback = useRef(onAutoSubmitted);

  useEffect(() => {
    callback.current = onAutoSubmitted;
  }, [onAutoSubmitted]);

  const apply = useCallback((s: IntegrityState) => {
    setState(s);
    if (!done.current && s.status !== "IN_PROGRESS" && s.submit_reason === "WARNING_THRESHOLD") {
      done.current = true;
      callback.current(s);
    }
  }, []);

  const report = useCallback(async (kind: Kind) => {
    if (!attemptId || done.current || unloading.current || Date.now() < suppressUntil.current) return;
    const body = JSON.stringify({ client_event_id: newId(), kind, occurred_at: new Date().toISOString() });
    // the same client_event_id on every retry: a report that reached the server but whose answer was lost counts once
    for (let i = 0; i <= RETRIES.length; i += 1) {
      try {
        const r = await fetch(`${apiUrl}/attempts/${attemptId}/integrity-events`, {
          method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body });
        if (r.ok) {
          apply(await r.json());
          return;
        }
        if (r.status < 500) return;                       // refused (e.g. not this student's attempt): nothing to retry
      } catch {
        /* network failure: retry below */
      }
      if (i < RETRIES.length) await new Promise((res) => setTimeout(res, RETRIES[i]));
    }
  }, [apiUrl, attemptId, apply]);

  useEffect(() => {
    if (!active || !attemptId) return;
    let cancelled = false;
    fetch(`${apiUrl}/attempts/${attemptId}/integrity`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((s: IntegrityState | null) => { if (s && !cancelled) apply(s); })
      .catch(() => { /* the count arrives with the next report */ });

    // a reload (or closing the page) also hides it; it is not leaving the exam: the attempt resumes where it was
    const onUnload = () => { unloading.current = true; };
    const onVisibility = () => { if (document.visibilityState === "hidden") void report("TAB_HIDDEN"); };
    // a tab switch also hides the page; the server counts one action once, so both may be reported
    const onBlur = () => { if (document.visibilityState === "visible") void report("WINDOW_BLUR"); };
    const onFullscreen = () => {
      if (document.fullscreenElement) {
        inFullscreen.current = true;
      } else if (inFullscreen.current) {
        inFullscreen.current = false;
        void report("FULLSCREEN_EXIT");
      }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    document.addEventListener("fullscreenchange", onFullscreen);
    return () => {
      cancelled = true;
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("fullscreenchange", onFullscreen);
    };
  }, [active, attemptId, apiUrl, report, apply]);

  /** Run one of the page's own dialogs (e.g. the submit confirmation) without it counting as leaving the exam. */
  const withoutWarnings = useCallback(<T,>(fn: () => T): T => {
    suppressUntil.current = Date.now() + 60_000;
    try {
      return fn();
    } finally {
      suppressUntil.current = Date.now() + 1000;
    }
  }, []);

  return { state, report, withoutWarnings };
}

export function enterFullscreen(): void {
  const el = document.documentElement;
  if (!document.fullscreenElement && el.requestFullscreen) {
    el.requestFullscreen().catch(() => { /* declined or unsupported: the exam continues, leaving is still reported */ });
  }
}
