/* An import review saved on the server as it is made (paper service: review drafts). Every decision is saved shortly after it is
   made; a reload, a closed tab or another day reopens the review where it was left. Two people editing one review never overwrite
   each other silently: a field someone else changed meanwhile is shown with both versions and the teacher chooses. */
import { useState } from "react";
import { useDraftAutosave, type DraftView, type SaveState } from "../hooks/useDraftAutosave";
import { ApiError, api } from "../services/api";
import { MathText } from "./MathText";
import { RecognitionReview } from "./recognitionReview";
import type { PreviewTarget } from "./RecognitionPreview";
import { FIELDS, type FieldName, type PaperPreview, type ServerProblem } from "./recognitionModel";
import { Alert, Button } from "./ui";

const LABEL: Record<FieldName, string> = { question: "Question", option_a: "Option A", option_b: "Option B", option_c: "Option C", option_d: "Option D", explanation: "Explanation" };
const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const fieldName = (key: string) => { const [n, f] = key.split(":"); return `Question ${n}, ${LABEL[f as FieldName] ?? f}`; };

function SaveStatus({ state, unsaved }: { state: SaveState; unsaved: boolean }) {
  const text = state.kind === "saving" ? "Saving…"
    : state.kind === "error" ? `Not saved - retrying (${state.message})`
    : state.kind === "conflict" ? "Someone else changed this review - choose below"
    : unsaved ? "Saving…" : `All changes saved at ${time(state.at)}`;
  return <p className={`small save-status${state.kind === "error" ? " error" : ""}`} role="status" aria-live="polite">{text}</p>;
}

/** The review of a saved draft: decisions autosaved, conflicts resolved by choice, imported from the stored file. */
export function DraftReview({ view, target, onImported, onClose }: { view: DraftView & { preview: PaperPreview }; target: PreviewTarget;
  onImported: (r: { paper_version_id: string; question_count: number }) => void; onClose: () => void }) {
  const { draft, preview } = view;
  const save = useDraftAutosave(draft);
  const [stale, setStale] = useState(() => new Set(view.stale_keys ?? []));
  const decide = (key: string, d: Parameters<typeof save.decide>[1]) => {
    setStale((s) => { if (!s.has(key)) return s; const next = new Set(s); next.delete(key); return next; });
    save.decide(key, d);                                    // deciding (or confirming) saves it with the current engine
  };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [problems, setProblems] = useState<ServerProblem[]>([]);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(undefined);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const importIt = () => run(async () => {
    await save.flush();
    setProblems([]);
    try {
      onImported(await api(`/api/v1/review-drafts/${draft.draft_id}/import`, { method: "POST", body: { revision: save.revision() } }));
    } catch (e) {
      if (e instanceof ApiError && e.code === "REVIEW_REQUIRED") {
        setProblems((e.extra.errors as ServerProblem[]) ?? []);
        throw new Error("Nothing was imported: some decisions still need attention (highlighted below).", { cause: e });
      }
      if (e instanceof ApiError && e.code === "REVIEW_STALE") {
        setStale(new Set((e.extra.stale_keys as string[]) ?? []));
        throw new Error("Some decisions were made before the recognition was improved: confirm each highlighted field, then import.", { cause: e });
      }
      if (e instanceof ApiError && e.code === "DRAFT_CONFLICT") throw new Error("Someone else changed this review just now. Reopen it to see their changes before importing.", { cause: e });
      throw e;
    }
  });
  const discard = () => run(async () => {
    await api(`/api/v1/review-drafts/${draft.draft_id}/discard`, { method: "POST" });
    onClose();
  });
  const conflictKeys = Object.keys(save.conflicts).filter((k) => FIELDS.includes(k.split(":")[1] as FieldName));
  return (
    <div className="stack">
      <SaveStatus state={save.state} unsaved={save.unsaved} />
      {view.resumed && <Alert kind="info">You had already started reviewing this file: your earlier decisions are restored.</Alert>}
      {stale.size > 0 && <Alert kind="warn">The recognition was improved since you saved this review. Your decisions are kept: check and confirm each highlighted field ({stale.size} left) before importing.</Alert>}
      {error && <Alert kind="error">{error}</Alert>}
      {conflictKeys.map((k) => {
        const theirs = save.conflicts[k].decision;
        return (
          <Alert key={k} kind="warn">
            <strong>{fieldName(k)}</strong> was changed by someone else at {time(save.conflicts[k].at)}. Their version:{" "}
            {theirs?.action === "edit" ? <MathText text={theirs.text} /> : theirs ? "keep the text exactly as in the file" : "no decision"}
            <div className="row">
              <Button small onClick={() => save.useTheirs(k)}>Use their version</Button>
              <Button small variant="secondary" onClick={() => save.keepMine(k)}>Keep mine</Button>
            </div>
          </Alert>
        );
      })}
      <RecognitionReview preview={preview} decisions={save.decisions} onDecide={decide} stale={stale} busy={busy} problems={problems}
        previewTarget={target} source={draft.file_name} blockedReason={conflictKeys.length ? "Resolve the changes above first" : save.unsaved ? "Saving your decisions…" : undefined}
        onImport={() => void importIt()} onCancel={onClose} cancelLabel="Close (continue later)" />
      <div className="row"><Button variant="secondary" small busy={busy} onClick={() => void discard()}>Discard this review</Button></div>
    </div>
  );
}
