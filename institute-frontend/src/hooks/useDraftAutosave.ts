/* The decisions of a saved import review (paper service: review drafts), saved shortly after each change. A field someone else
   changed meanwhile is never overwritten: the save is refused (DRAFT_CONFLICT) and both versions are kept for the teacher to choose. */
import { useCallback, useEffect, useRef, useState } from "react";
import type { Decision, PaperPreview } from "../components/recognitionModel";
import { ApiError, api } from "../services/api";

export type DraftEntry = { decision: Decision | null; rev: number; by: string; at: string };
export type ReviewDraft = { draft_id: string; status: "OPEN" | "IMPORTED" | "DISCARDED"; title: string; file_name: string; subject_id: string;
  revision: number; decisions: Record<string, DraftEntry>; updated_at: string; result_paper_version_id: string | null; has_answer_key: boolean };
export type DraftView = { draft: ReviewDraft; preview?: PaperPreview; resumed?: boolean; engine_changed?: boolean;
  stale_keys?: string[] };            // decisions made with an older recognition engine: confirmed again before import
export type SaveState = { kind: "saved"; at: string } | { kind: "saving" } | { kind: "error"; message: string } | { kind: "conflict" };
type Changes = Record<string, Decision | null>;

const RETRY_MS = 5000;
const decisionsOf = (entries: Record<string, DraftEntry>): Record<string, Decision> =>
  Object.fromEntries(Object.entries(entries).flatMap(([k, e]) => (e.decision ? [[k, e.decision]] : [])));
const withDecision = (cur: Record<string, Decision>, key: string, d: Decision | null) => {
  const next = { ...cur };
  if (d) next[key] = d; else delete next[key];
  return next;
};
const without = <T,>(cur: Record<string, T>, key: string) => Object.fromEntries(Object.entries(cur).filter(([k]) => k !== key));

/** `delay`: ms after the last change before everything changed meanwhile is saved in one request. */
export function useDraftAutosave(draft: ReviewDraft, delay = 1500) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>(() => decisionsOf(draft.decisions));
  const [state, setState] = useState<SaveState>({ kind: "saved", at: draft.updated_at });
  const [conflicts, setConflicts] = useState<Record<string, DraftEntry>>({});
  const [dirty, setDirty] = useState(false);                          // changes not yet sent
  const revision = useRef(draft.revision);
  const pending = useRef<Changes>({});
  const inFlight = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flushRef = useRef<() => Promise<void>>(async () => {});
  const schedule = useCallback((ms: number) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void flushRef.current(), ms);
  }, []);

  const flush = useCallback(async (): Promise<void> => {
    clearTimeout(timer.current);
    if (inFlight.current || !Object.keys(pending.current).length) return;
    const send = pending.current;
    pending.current = {};
    inFlight.current = true;
    setDirty(false);
    setState({ kind: "saving" });
    try {
      const r = await api<{ revision: number; updated_at: string }>(`/api/v1/review-drafts/${draft.draft_id}`,
        { method: "PATCH", body: { base_revision: revision.current, changes: send } });
      revision.current = r.revision;
      setState({ kind: "saved", at: r.updated_at });
    } catch (e) {
      const unsent = { ...send, ...pending.current };                    // anything changed meanwhile is newer than what was sent
      if (e instanceof ApiError && e.code === "DRAFT_CONFLICT") {
        const theirs = (e.extra.conflicts ?? {}) as Record<string, DraftEntry>;
        revision.current = Number(e.extra.revision);                      // seen now: the next save is based on it
        pending.current = Object.fromEntries(Object.entries(unsent).filter(([k]) => !(k in theirs)));
        setConflicts((c) => ({ ...c, ...theirs }));
        setState({ kind: "conflict" });
      } else {
        pending.current = unsent;
        setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
        schedule(RETRY_MS);
      }
    } finally {
      inFlight.current = false;
    }
    if (Object.keys(pending.current).length) {
      setDirty(true);
      schedule(delay);
    }
  }, [draft.draft_id, delay, schedule]);
  useEffect(() => { flushRef.current = flush; }, [flush]);

  const queue = (changes: Changes) => {
    pending.current = { ...pending.current, ...changes };
    setDirty(true);
    schedule(delay);
  };
  const decide = (key: string, d: Decision | null) => {
    setDecisions((cur) => withDecision(cur, key, d));
    queue({ [key]: d });
  };
  const resolved = (key: string) => {
    const rest = without(conflicts, key);
    setConflicts(rest);
    if (!Object.keys(rest).length) setState({ kind: "saved", at: new Date().toISOString() });
  };
  /** Someone else's version replaces this teacher's (it is already saved). */
  const useTheirs = (key: string) => {
    setDecisions((cur) => withDecision(cur, key, conflicts[key]?.decision ?? null));
    resolved(key);
  };
  /** This teacher's version is saved over theirs, deliberately (theirs has been seen). */
  const keepMine = (key: string) => {
    resolved(key);
    queue({ [key]: decisions[key] ?? null });
  };
  const currentRevision = useCallback(() => revision.current, []);

  const unsaved = dirty || state.kind === "saving" || state.kind === "error" || Object.keys(conflicts).length > 0;
  useEffect(() => {
    if (!unsaved) return undefined;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };   // leaving now would lose unsaved decisions
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);
  useEffect(() => () => clearTimeout(timer.current), []);

  return { decisions, decide, state, conflicts, useTheirs, keepMine, unsaved, revision: currentRevision, flush };
}
