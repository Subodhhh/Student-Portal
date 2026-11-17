/* Data of the paper import preview and the rule for which fields still need a provider decision. */
import { mathProblems } from "./mathRender";

export type Outcome = "CONVERTED" | "PLAIN_TEXT" | "NEEDS_REVIEW" | "EXTRACTION_ERROR";
export type RecognitionSpan = { original: string; status: string; latex: string | null; code: string | null; reason: string | null; domain: string | null;
  start?: number; end?: number;              // where the expression is in `original` (records stored before engine 2.4.0 have none)
  explanation?: string | null };             // what the teacher is told; the code and the technical reason stay in Diagnostics
export type Suggestion = { expression: string; label: string; text: string };
/** How complete the list of readings for one expression is: `complete` = every reading the engine's grammar allows was checked. */
export type CandidateSet = { expression: string; shown: number; combinations: number; checked: number; rejected: number; complete: boolean; limit: number };
export type RecognitionField = { original: string; text: string; outcome: Outcome; reasons: string[]; spans: RecognitionSpan[];
  suggestions?: Suggestion[];      // every checked reading of an expression the engine would not decide: picking one fills the correction
  candidate_sets?: CandidateSet[] };
/** A picture in the uploaded paper: the question's ("question") or an option's ("A".."D"). asset_id is set once it is stored. */
export type QuestionImage = { asset_id?: string; name: string; width: number; height: number; mime: string };
export type ImageField = "question" | "A" | "B" | "C" | "D";
export type ReviewQuestion = { number: number; row: number; correct: string; marks: string; negative_marks: string; topic: string | null; status: string;
  chapter?: string | null; sub_topic?: string | null; explanation?: string | null;
  images?: Partial<Record<ImageField, QuestionImage>>;
  fields: Partial<Record<FieldName, RecognitionField>> };
export type PaperPreview = { review_token: string; engine_version: string; domain: string | null; questions: ReviewQuestion[];
  summary: { questions: number; questions_needing_review: number; fields: Record<Outcome, number> } };
export type Decision = { action: "keep_original" } | { action: "edit"; text: string };
export type FieldName = "question" | "option_a" | "option_b" | "option_c" | "option_d" | "explanation";
export type ServerProblem = { question?: string | null; field?: string | null; message: string };

export const FIELDS: FieldName[] = ["question", "option_a", "option_b", "option_c", "option_d", "explanation"];
export const BLOCKING: Outcome[] = ["NEEDS_REVIEW", "EXTRACTION_ERROR"];

export const decisionKey = (n: number, f: FieldName) => `${n}:${f}`;

/** Decisions still missing (or invalid) for the fields that block the import. */
export function openItems(preview: PaperPreview, decisions: Record<string, Decision>, stale: Set<string> = new Set()): string[] {
  const open: string[] = [];
  for (const q of preview.questions) {
    for (const f of FIELDS) {
      const rec = q.fields[f];
      if (!rec) continue;
      const d = decisions[decisionKey(q.number, f)];
      if (d && stale.has(decisionKey(q.number, f))) open.push(decisionKey(q.number, f));     // made with an older engine: confirm again
      else if (d?.action === "edit" && (!d.text.trim() || mathProblems(d.text).length)) open.push(decisionKey(q.number, f));
      else if (!d && BLOCKING.includes(rec.outcome)) open.push(decisionKey(q.number, f));
    }
  }
  return open;
}

