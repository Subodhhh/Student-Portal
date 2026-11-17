/* Live preview of a correction while it is typed: the server recognises the text exactly as an import or a saved correction would
   (same engine, same subject rules) and the result is typeset with MathText, the component every Review screen uses - so what is shown
   is what will be saved. Plain notation needs no LaTeX: x^2, (x+1)/(x-1), [[1,0],[0,2]], H2SO4 ... A doubt is explained, with each
   reading typeset; choosing one only fills the text, which is checked again when it is saved. */
import { useEffect, useState } from "react";
import { api } from "../services/api";
import { MathText } from "./MathText";
import type { FieldName, Outcome, Suggestion } from "./recognitionModel";
import { Badge, Button } from "./ui";

export type PreviewResult = { original: string; text: string; outcome: Outcome; reasons: string[];
  spans: { original: string; status: string; code: string | null; reason: string | null; explanation?: string | null }[]; suggestions: Suggestion[] };

const LABEL: Record<Outcome, string> = { CONVERTED: "Formatted", PLAIN_TEXT: "Plain text", NEEDS_REVIEW: "Needs review", EXTRACTION_ERROR: "Not extracted" };
const TONE: Record<Outcome, "ok" | "info" | "warn" | "danger"> = { CONVERTED: "ok", PLAIN_TEXT: "info", NEEDS_REVIEW: "warn", EXTRACTION_ERROR: "danger" };

/** Where the text is recognised: the import being reviewed (its branch and subject) or the paper version being corrected. */
export type PreviewTarget = { branchId: string; subjectId: string } | { versionId: string };

function request(target: PreviewTarget, text: string, field: FieldName): Promise<PreviewResult> {
  const body = { text, field };
  return "versionId" in target
    ? api<PreviewResult>(`/api/v1/paper-versions/${target.versionId}/recognition-preview`, { method: "POST", body })
    : api<PreviewResult>(`/api/v1/branches/${target.branchId}/subjects/${target.subjectId}/recognition-preview`, { method: "POST", body });
}

export function RecognitionPreview({ target, text, field, onPick, delay = 350 }: { target: PreviewTarget; text: string; field: FieldName;
  onPick?: (text: string) => void; delay?: number }) {
  const [state, setState] = useState<{ for: string; result?: PreviewResult; error?: string } | null>(null);
  const key = "versionId" in target ? target.versionId : `${target.branchId}/${target.subjectId}`;
  useEffect(() => {
    if (!text.trim()) return undefined;
    let live = true;
    const timer = setTimeout(() => {
      request(target, text, field)
        .then((result) => { if (live) setState({ for: text, result }); })
        .catch((e: unknown) => { if (live) setState({ for: text, error: e instanceof Error ? e.message : String(e) }); });
    }, delay);
    return () => { live = false; clearTimeout(timer); };
  }, [key, text, field, delay]);   // eslint-disable-line react-hooks/exhaustive-deps -- target is identified by key
  if (!text.trim()) return null;
  const current = state?.for === text ? state : null;
  const r = current?.result;
  return (
    <div className="recognition-preview stack" aria-live="polite" data-testid={`live-preview-${field}`}>
      <div className="row between small">
        <span className="muted">Preview, as it will be saved{current ? "" : " (updating…)"}</span>
        {r && <Badge tone={TONE[r.outcome]}>{LABEL[r.outcome]}</Badge>}
      </div>
      {current?.error && <span className="small" role="alert">{current.error}</span>}
      {r && <MathText as="div" text={r.text} />}
      {r && r.spans.filter((s) => s.status === "flagged" || s.status === "error").map((s, k) => (
        <div key={k} className="small"><code>{s.original}</code>: {s.explanation ?? s.reason}</div>
      ))}
      {r && onPick && [...new Set(r.suggestions.map((s) => s.expression))].map((expr) => (
        <div key={expr} className="recognition-suggestions" role="group" aria-label={`How should ${expr} be read?`}>
          <span className="small">How should <code>{expr}</code> be read?</span>
          {r.suggestions.filter((s) => s.expression === expr).map((s) => (
            <Button key={s.label} small variant="secondary" aria-label={`Use the reading ${s.label}`} onClick={() => onPick(s.text)}>
              <MathText text={s.text} /> <span className="muted">({s.label})</span>
            </Button>
          ))}
        </div>
      ))}
    </div>
  );
}
