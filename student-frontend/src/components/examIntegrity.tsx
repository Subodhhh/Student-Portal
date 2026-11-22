import { useCallback, useEffect, useRef, useState } from "react";

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

type Kind =
  | "TAB_HIDDEN" | "WINDOW_BLUR" | "FULLSCREEN_EXIT"
  | "COPY" | "PASTE" | "CUT" | "KEYBOARD_SHORTCUT"
  | "CONTEXT_MENU" | "BACK" | "REFRESH";

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
  const suppressPageHide = useRef(false);
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
    
    for (let i = 0; i <= RETRIES.length; i += 1) {
      try {
        const r = await fetch(`${apiUrl}/attempts/${attemptId}/integrity-events`, {
          method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body });
        if (r.ok) {
          apply(await r.json());
          return;
        }
        if (r.status < 500) return;
      } catch {
        /* Network failure: retry below */
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
      .catch(() => { /* The count arrives with the next report */ });

    const onUnload = (event: BeforeUnloadEvent) => {
      unloading.current = true;
      window.setTimeout(() => { unloading.current = false; }, 200);
      event.preventDefault();
      event.returnValue = "";
      return "";
    };
    
    const onVisibility = () => { if (document.visibilityState === "hidden") void report("TAB_HIDDEN"); };
    const onBlur = () => { if (document.visibilityState === "visible") void report("WINDOW_BLUR"); };
    
    const onFullscreen = () => {
      if (document.fullscreenElement) {
        inFullscreen.current = true;
      } else if (inFullscreen.current) {
        inFullscreen.current = false;
        void report("FULLSCREEN_EXIT");
      }
    };

    // Block Clipboard
    const onCopy = (event: ClipboardEvent) => { event.preventDefault(); void report("COPY"); };
    const onPaste = (event: ClipboardEvent) => { event.preventDefault(); void report("PASTE"); };
    const onCut = (event: ClipboardEvent) => { event.preventDefault(); void report("CUT"); };

    // Block Right Click, Text Selection, and Dragging
    const onContextMenu = (event: MouseEvent) => { event.preventDefault(); void report("CONTEXT_MENU"); };
    const onSelectStart = (event: Event) => { event.preventDefault(); };
    const onDragStart = (event: Event) => { event.preventDefault(); };

    // Block Keyboard Shortcuts (Copy, Paste, Cut, Select All, Print, Inspect, View Source)
    const onKeyDown = (event: KeyboardEvent) => {
      const isCmdOrCtrl = event.ctrlKey || event.metaKey;
      
      // Block Ctrl+C, V, X, A, P, S, U
      if (isCmdOrCtrl && ["c", "v", "x", "a", "p", "s", "u"].includes(event.key.toLowerCase())) {
        event.preventDefault();
        void report("KEYBOARD_SHORTCUT");
      }
      // Block F12, Ctrl+Shift+I (Inspect), Ctrl+Shift+J (Console), Ctrl+Shift+C (Element Picker)
      if (
        event.key === "F12" || 
        (isCmdOrCtrl && event.shiftKey && ["i", "j", "c"].includes(event.key.toLowerCase()))
      ) {
        event.preventDefault();
        void report("KEYBOARD_SHORTCUT");
      }
    };

    try { 
      history.pushState({ examGuard: true }, "", location.href); 
    } catch { 
      /* Ignored: History API may fail in some browsers */ 
    }
    
    const onPopState = () => {
      try { 
        history.pushState({ examGuard: true }, "", location.href); 
      } catch { 
        /* Ignored */ 
      }
      suppressPageHide.current = true;
      window.setTimeout(() => { suppressPageHide.current = false; }, 500);
      void report("BACK");
    };

    const onPageHide = () => {
      if (!attemptId || done.current || suppressPageHide.current) return;
      try {
        void fetch(`${apiUrl}/attempts/${attemptId}/integrity-events`, {
          method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_event_id: newId(), kind: "REFRESH", occurred_at: new Date().toISOString() }),
          keepalive: true,
        }).catch(() => { /* Page is going away; server records what arrived */ });
      } catch { 
        /* Ignored */ 
      }
    };

    // Use capture: true to intercept events before they reach the page
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    document.addEventListener("fullscreenchange", onFullscreen);
    document.addEventListener("copy", onCopy, { capture: true });
    document.addEventListener("paste", onPaste, { capture: true });
    document.addEventListener("cut", onCut, { capture: true });
    document.addEventListener("contextmenu", onContextMenu, { capture: true });
    document.addEventListener("selectstart", onSelectStart, { capture: true });
    document.addEventListener("dragstart", onDragStart, { capture: true });
    document.addEventListener("keydown", onKeyDown, { capture: true });
    window.addEventListener("popstate", onPopState);
    window.addEventListener("pagehide", onPageHide);

    return () => {
      cancelled = true;
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("fullscreenchange", onFullscreen);
      document.removeEventListener("copy", onCopy, { capture: true });
      document.removeEventListener("paste", onPaste, { capture: true });
      document.removeEventListener("cut", onCut, { capture: true });
      document.removeEventListener("contextmenu", onContextMenu, { capture: true });
      document.removeEventListener("selectstart", onSelectStart, { capture: true });
      document.removeEventListener("dragstart", onDragStart, { capture: true });
      document.removeEventListener("keydown", onKeyDown, { capture: true });
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [active, attemptId, apiUrl, report, apply]);

  const withoutWarnings = useCallback(<T,>(fn: () => T): T => {
    suppressUntil.current = Date.now() + 60_000;
    try { return fn(); } finally { suppressUntil.current = Date.now() + 1000; }
  }, []);

  return { state, report, withoutWarnings };
}

export function enterFullscreen(): void {
  const el = document.documentElement;
  if (!document.fullscreenElement && el.requestFullscreen) {
    el.requestFullscreen().catch(() => { /* Ignored */ });
  }
}