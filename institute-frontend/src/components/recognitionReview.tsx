/* Provider preview of an uploaded paper before import: every question as students will see it (KaTeX), the recognition outcome of
   every field, and a decision for each field that needs attention. Nothing is imported until every flagged field has a decision;
   the server checks every decision again (an edit must itself pass recognition) and binds them to the previewed file. */
import { useMemo, useState } from "react";
import { MathText } from "./MathText";
import { RecognitionPreview, type PreviewTarget } from "./RecognitionPreview";
import { mathProblems } from "./mathRender";
import { Alert, Badge, Button, Card } from "./ui";
import { API_URL } from "../services/api";
import { BLOCKING, FIELDS, decisionKey, openItems, type CandidateSet, type Decision, type FieldName, type ImageField, type Outcome, type PaperPreview, type QuestionImage, type RecognitionField, type ReviewQuestion, type ServerProblem, type Suggestion } from "./recognitionModel";

const LABEL: Record<FieldName, string> = { question: "Question", option_a: "Option A", option_b: "Option B", option_c: "Option C", option_d: "Option D", explanation: "Explanation" };
const OUTCOME_LABEL: Record<Outcome, string> = { CONVERTED: "Formatted", PLAIN_TEXT: "Plain text", NEEDS_REVIEW: "Needs review", EXTRACTION_ERROR: "Not extracted" };
const OUTCOME_TONE: Record<Outcome, "ok" | "info" | "warn" | "danger"> = { CONVERTED: "ok", PLAIN_TEXT: "info", NEEDS_REVIEW: "warn", EXTRACTION_ERROR: "danger" };

const SHOWN_AT_FIRST = 6;

/** Every checked reading of one expression, typeset as students would see it. The teacher picks the one the paper means; the
 *  list says whether it is every possible reading or (above the engine's limit) only the first ones checked. */
function Readings({ expression, readings, set, onPick }: { expression: string; readings: Suggestion[]; set?: CandidateSet; onPick: (text: string) => void }) {
  const [all, setAll] = useState(false);
  const shown = all ? readings : readings.slice(0, SHOWN_AT_FIRST);
  return (
    <div className="recognition-suggestions" role="group" aria-label={`How should ${expression} be read?`}>
      <span className="small">How should <code>{expression}</code> be read? Choose the one your paper means.</span>
      <ol className="recognition-readings">
        {shown.map((r, k) => (
          <li key={r.text}>
            <Button small variant="secondary" aria-label={`Use reading ${k + 1}: ${r.label}`} onClick={() => onPick(r.text)}>
              <MathText text={r.text} />
            </Button>
            <span className="small muted">{r.label}</span>
          </li>
        ))}
      </ol>
      {readings.length > SHOWN_AT_FIRST && (
        <Button small variant="secondary" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${readings.length} readings`}
        </Button>
      )}
      <p className="small muted recognition-completeness">
        {set && !set.complete
          ? `${readings.length} reading(s) shown. This expression can be read in ${set.combinations} ways, more than are checked at once; if yours is not listed, correct it below.`
          : readings.length === 1 ? "This is the only way it can be read." : `These are all ${readings.length} ways it can be read.`}
      </p>
    </div>
  );
}

const IMAGE_LABEL: Record<ImageField, string> = { question: "Question picture", A: "Option A picture", B: "Option B picture", C: "Option C picture", D: "Option D picture" };

/** A picture from the uploaded paper, exactly as uploaded and as students will see it (its contents are never read). */
export function QuestionPicture({ image, field }: { image: QuestionImage; field: ImageField }) {
  if (!image.asset_id) return <p className="small muted">{IMAGE_LABEL[field]}: {image.name}</p>;
  return (
    <figure className="question-picture">
      <img src={`${API_URL}/api/v1/question-images/${image.asset_id}`} alt={`${IMAGE_LABEL[field]} (${image.name})`}
        width={image.width} height={image.height} loading="lazy" />
    </figure>
  );
}

function FieldReview({ q, f, rec, decision, onDecide, problem, previewTarget, stale }: { q: ReviewQuestion; f: FieldName; rec: RecognitionField; decision?: Decision;
  onDecide: (d: Decision | null) => void; problem?: string; previewTarget?: PreviewTarget; stale?: boolean }) {
  const blocking = BLOCKING.includes(rec.outcome);
  const [showOriginal, setShowOriginal] = useState(false);
  const editing = decision?.action === "edit";
  const editProblems = editing ? mathProblems(decision.text) : [];
  const id = `rev-${q.number}-${f}`;
  return (
    <div className={`recognition-field${(blocking && !decision) || (stale && decision) ? " needs-attention" : ""}`} data-testid={id}>
      <div className="row between">
        <strong>{LABEL[f]}{f !== "question" && f !== "explanation" && q.correct === f.slice(-1).toUpperCase() ? " (correct answer)" : ""}</strong>
        <Badge tone={OUTCOME_TONE[rec.outcome]}>{OUTCOME_LABEL[rec.outcome]}</Badge>
      </div>
      {!blocking && !decision && <MathText as="div" text={rec.text} />}
      {!blocking && rec.outcome === "CONVERTED" && !decision && (
        <Button small variant="secondary" onClick={() => setShowOriginal(!showOriginal)}>{showOriginal ? "Hide" : "Show"} what the file contains</Button>
      )}
      {(blocking || showOriginal || decision) && (
        <div className="small"><span className="muted">In the file: </span><code className="recognition-original">{rec.original}</code></div>
      )}
      {blocking && (
        <ul className="small recognition-reasons">
          {rec.spans.filter((s) => s.status === "flagged" || s.status === "error").map((s, k) => (
            <li key={k}><code>{s.original}</code>: {s.explanation ?? s.reason}</li>
          ))}
        </ul>
      )}
      {blocking && !!rec.suggestions?.length && (
        [...new Set(rec.suggestions.map((sg) => sg.expression))].map((expr) => (
          <Readings key={expr} expression={expr} readings={rec.suggestions!.filter((sg) => sg.expression === expr)}
            set={rec.candidate_sets?.find((c) => c.expression === expr)} onPick={(text) => onDecide({ action: "edit", text })} />
        ))
      )}
      {problem && <Alert kind="error">{problem}</Alert>}
      {stale && decision && (
        <Alert kind="warn">The recognition was improved after you decided this field. Check your decision below, then confirm it.{" "}
          <Button small onClick={() => onDecide(decision)}>Confirm this decision</Button></Alert>
      )}
      <div className="row recognition-actions" role="group" aria-label={`Decision for ${LABEL[f]} of question ${q.number}`}>
        {(blocking || decision) && rec.outcome !== "EXTRACTION_ERROR" && (
          <label className="row checkbox-field"><input type="radio" name={id} checked={decision?.action === "keep_original"}
            onChange={() => onDecide({ action: "keep_original" })} /> Keep the text exactly as in the file (plain text)</label>
        )}
        {rec.outcome === "EXTRACTION_ERROR" && (
          <p className="small">Part of this field could not be read from the file as text, so it cannot be kept as it is: correct it.</p>
        )}
        <label className="row checkbox-field"><input type="radio" name={id} checked={editing}
          onChange={() => onDecide({ action: "edit", text: rec.original })} /> Correct it</label>
        {!blocking && decision && <Button small variant="secondary" onClick={() => onDecide(null)}>Use the formatted version</Button>}
        {!blocking && !decision && rec.outcome === "CONVERTED" && (
          <Button small variant="secondary" onClick={() => onDecide({ action: "keep_original" })}>Keep as typed instead</Button>
        )}
      </div>
      {decision?.action === "keep_original" && <MathText as="div" className="recognition-preview" text={rec.original} />}
      {editing && (
        <div className="stack">
          <label htmlFor={`${id}-edit`} className="small">Corrected text: type it as you would on paper (x^2, (x+1)/(x-1), [[1,0],[0,2]], ∫₀^π sin x dx) - the preview shows how students will see it.</label>
          <textarea id={`${id}-edit`} rows={2} value={decision.text} onChange={(e) => onDecide({ action: "edit", text: e.target.value })} />
          {previewTarget
            ? <RecognitionPreview target={previewTarget} text={decision.text} field={f} onPick={(t) => onDecide({ action: "edit", text: t })} />
            : <><div className="small muted">Preview (checked again on import):</div><MathText as="div" className="recognition-preview" text={decision.text} /></>}
          {editProblems.map((m) => <Alert key={m} kind="error">{m}</Alert>)}
        </div>
      )}
    </div>
  );
}

export function RecognitionReview({ preview, onImport, onCancel, busy, problems = [], previewTarget, source, decisions: controlled, onDecide,
  blockedReason, cancelLabel = "Cancel", stale }: { preview: PaperPreview;
  onImport: (decisions: Record<string, Decision>) => void; onCancel: () => void; busy?: boolean; problems?: ServerProblem[];
  previewTarget?: PreviewTarget; source?: string;
  decisions?: Record<string, Decision>; onDecide?: (key: string, d: Decision | null) => void;   // given: the decisions live outside (a saved draft)
  blockedReason?: string; cancelLabel?: string; stale?: Set<string> }) {
  const [own, setOwn] = useState<Record<string, Decision>>({});
  const decisions = controlled ?? own;
  const [onlyAttention, setOnlyAttention] = useState(preview.summary.questions_needing_review > 0);
  const open = useMemo(() => openItems(preview, decisions, stale), [preview, decisions, stale]);
  const byField = new Map(problems.filter((p) => p.question && p.field).map((p) => [`${p.question}:${p.field}`, p.message]));
  const general = problems.filter((p) => !p.question || !p.field);
  const decide = (k: string, d: Decision | null) => (onDecide ? onDecide(k, d) : setOwn((cur) => {
    const next = { ...cur };
    if (d) next[k] = d; else delete next[k];
    return next;
  }));
  const shown = preview.questions.filter((q) => !onlyAttention || q.status !== "READY"
    || FIELDS.some((f) => byField.has(decisionKey(q.number, f)) || stale?.has(decisionKey(q.number, f))));
  const s = preview.summary;
  return (
    <Card className="recognition-review">
      <h2>Preview before import</h2>
      {source && <p className="small muted">Source: {source}</p>}
      <p className="small">
        {s.questions} questions. Fields: {s.fields.CONVERTED} formatted, {s.fields.PLAIN_TEXT} plain text, {s.fields.NEEDS_REVIEW} need review,
        {" "}{s.fields.EXTRACTION_ERROR} could not be extracted. Nothing is imported until you confirm.
      </p>
      {s.questions_needing_review > 0
        ? <Alert kind="warn">{s.questions_needing_review} question(s) contain notation that could be read more than one way or could not be extracted. Choose how each
          highlighted field should appear; formatted text is never guessed.</Alert>
        : <Alert kind="success">Every field was read with certainty. Check the formatting below, then import.</Alert>}
      {general.map((p) => <Alert key={p.message} kind="error">{p.message}</Alert>)}
      <label className="row checkbox-field"><input type="checkbox" checked={onlyAttention} onChange={(e) => setOnlyAttention(e.target.checked)} /> Show only questions that need attention</label>
      {shown.map((q) => (
        <article key={q.number} className="recognition-question" aria-label={`Question ${q.number}`}>
          <div className="row between">
            <h3>Question {q.number}</h3>
            <span className="small muted">Answer {q.correct} · +{q.marks} / −{q.negative_marks}</span>
          </div>
          {(q.chapter || q.topic || q.sub_topic) && (
            <p className="small muted">{[q.chapter, q.topic, q.sub_topic].filter(Boolean).join(" › ")}</p>
          )}
          {FIELDS.map((f) => {
            const rec = q.fields[f];
            const k = decisionKey(q.number, f);
            const pictureField: ImageField | null = f === "question" ? "question" : f.startsWith("option_") ? (f.slice(-1).toUpperCase() as ImageField) : null;
            const picture = pictureField ? q.images?.[pictureField] : undefined;
            return (
              <div key={f}>
                {rec && <FieldReview q={q} f={f} rec={rec} decision={decisions[k]} onDecide={(d) => decide(k, d)} problem={byField.get(k)} previewTarget={previewTarget}
                  stale={stale?.has(k)} />}
                {picture && pictureField && (
                  <div className="recognition-field" data-testid={`pic-${q.number}-${pictureField}`}>
                    {!rec && <strong>{LABEL[f]}{f !== "question" && q.correct === pictureField ? " (correct answer)" : ""}</strong>}
                    <QuestionPicture image={picture} field={pictureField} />
                  </div>
                )}
              </div>
            );
          })}
        </article>
      ))}
      <div className="row">
        <Button busy={busy} disabled={open.length > 0 || !!blockedReason} onClick={() => onImport(decisions)}>
          {open.length ? `${open.length} field(s) still need a decision` : blockedReason ?? "Import as a draft paper"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>{cancelLabel}</Button>
      </div>
    </Card>
  );
}
