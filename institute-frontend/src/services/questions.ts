import { ApiError, api } from "./api";

// Question-bank types and helpers shared by the Question Bank page and the test-creation wizard.

export type QuestionVersion = {
  question_version_id: string; question_id: string; version_no: number; stem: string; option_a: string; option_b: string; option_c: string; option_d: string;
  correct_option: string | null; explanation: string | null; topic: string | null; marks_positive: string | null; marks_negative: string | null;   // null: marks are decided by the test
  answers_hidden?: boolean;   // a Global Test question before the provider's review point
  // pictures, served by the authorised GET /question-images/{id}; null: the field has no picture
  stem_image_id?: string | null; option_a_image_id?: string | null; option_b_image_id?: string | null;
  option_c_image_id?: string | null; option_d_image_id?: string | null;
  // MCQ (the default): the columns above. MSQ / NAT (QUESTION_TYPES_ARCHITECTURE.md): `content` and `answer_key` (null when hidden)
  question_type?: QuestionType; content?: MsqContent | NatContent | null; answer_key?: MsqKey | NatKey | null;
};
export type QuestionType = "MCQ_SINGLE" | "MCQ_MULTI" | "NUMERICAL";
export type MsqContent = { options: { id: string; text: string | null; image?: string | null }[] };
export type MsqKey = { correct: string[] };
export type NatContent = { format: "INTEGER" | "DECIMAL"; decimals: number; unit: string | null };
export type NatKey = { value: string; tolerance: { kind: "EXACT" | "ABS" | "RANGE"; amount?: string; min?: string; max?: string } };

export const TYPE_LABEL: Record<QuestionType, string> = { MCQ_SINGLE: "MCQ", MCQ_MULTI: "MSQ", NUMERICAL: "NAT" };
export const TYPE_NAME: Record<QuestionType, string> = {
  MCQ_SINGLE: "MCQ - one correct option", MCQ_MULTI: "MSQ - one or more correct options", NUMERICAL: "NAT - a number answer" };
export const MSQ_LETTERS = "ABCDEFGH";

/** The key in words, as the server's describe_key: "B", "A, C", "9.8 ± 0.05", "14.7 (accepted 14.5 to 15)". */
export function describeKey(type: QuestionType | undefined, v: { correct_option?: string | null; answer_key?: MsqKey | NatKey | null }): string {
  if (!type || type === "MCQ_SINGLE") return v.correct_option ?? "";
  if (!v.answer_key) return "";
  if (type === "MCQ_MULTI") return (v.answer_key as MsqKey).correct.join(", ");
  const k = v.answer_key as NatKey;
  if (k.tolerance.kind === "ABS") return `${k.value} ± ${k.tolerance.amount}`;
  if (k.tolerance.kind === "RANGE") return `${k.value} (accepted ${k.tolerance.min} to ${k.tolerance.max})`;
  return k.value;
}

/** A NAT number as the server accepts it (core/question_types.parse_number + the answer format): digits, an optional minus and one
 * decimal point, at most 6 decimal places and 12 significant digits, never an exponent, a comma or a unit; then at most `decimals`
 * significant decimal places (whole numbers for `integer`). null when valid, else why not. The server checks it again. */
export function numberProblem(text: string, decimals = 6, integer = false): string | null {
  const m = /^-?(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!m) return "Enter a plain number such as 12, -3 or 0.25 (no letters, exponents, spaces or thousands separators).";
  const whole = m[1], fraction = m[2] ?? "";
  if (fraction.length > 6) return "Use at most 6 decimal places.";
  const significant = whole.replace(/^0+/, "") ? whole.replace(/^0+/, "") + fraction : fraction.replace(/^0+/, "");
  if (significant.length > 12) return "Use at most 12 significant digits.";
  const places = fraction.replace(/0+$/, "").length;
  if (integer && places) return "This answer must be a whole number.";
  if (places > decimals) return `Use at most ${decimals} decimal places (the answer format).`;
  return null;
}
export type BankQuestion = {
  question_id: string; scope: "PROVIDER" | "GLOBAL"; provider_id: string | null; branch_id: string | null; subject_id: string; status: string;
  exam_type: string | null; target_class: string | null; chapter: string | null; topic: string | null; subtopic: string | null; difficulty: string | null;
  pattern: string; subtype: string | null; question_type: string; created_at: string; latest: QuestionVersion; usage_count: number; can_edit: boolean;
  reserved_for_global_test?: boolean; forked_from_question_id?: string | null;
  origin_branch_name?: string | null;   // the branch that created a branch question (null: platform or provider-wide)
};
export type Source = "ALL" | "GLOBAL" | "PROVIDER";

export const PATTERN_LABEL: Record<string, string> = {
  SINGLE_CORRECT: "Single correct", ASSERTION_REASON: "Assertion & reason", STATEMENT_BASED: "Statement based",
  MATCH_THE_COLUMN: "Match the column", DIAGRAM_BASED: "Diagram based", CASE_BASED: "Case based",
};
export type BuiltPaper = { subject_id: string; paper_id: string; paper_version_id: string; version_no: number; status: string; question_count: number; total_marks: string };

/** Turn selected questions into papers: one paper per subject (a paper covers exactly one subject; a multi-subject test combines
 * several papers). Each question is referenced at its exact latest version - nothing is copied. */
export async function buildPapers(scope: "GLOBAL" | "PROVIDER", branchId: string | undefined, groups: { subject_id: string; title: string; question_ids: string[] }[],
  meta: { exam_type?: string | null; target_class?: string | null } = {}, onBuilt?: (b: BuiltPaper) => void): Promise<BuiltPaper[]> {
  const out: BuiltPaper[] = [];
  for (const g of groups) {
    const r = await api<Omit<BuiltPaper, "subject_id">>("/api/v1/papers", { method: "POST", body: {
      scope, ...(scope === "PROVIDER" ? { branch_id: branchId } : {}), subject_id: g.subject_id, title: g.title, question_ids: g.question_ids,
      exam_type: meta.exam_type || null, target_class: meta.target_class || null } });
    out.push({ ...r, subject_id: g.subject_id });
    onBuilt?.(out[out.length - 1]);
  }
  return out;
}

/** Possible duplicates the server found among one paper's questions (same question with reordered options, or the same long question
 * text with other options). They never block: the user looks and may build anyway. Identical questions are refused by the server. */
export type ReviewFlag = { positions: number[]; reason: string };
/** `blocking`: identical questions (same text and pictures) - the paper is refused until one of them is removed.
 * `flags`: possible duplicates - shown for a look; "Create anyway" builds. Positions are 1-based in `question_ids`. */
export type SubjectReview = { subject_id: string; question_ids: string[]; flags: ReviewFlag[]; blocking: ReviewFlag[] };

const SAME = /^Same question as position (\d+)\.$/;

/** The server's refusal of identical questions (422 INVALID_QUESTIONS, "Same question as position N.") as pairs; null when the
 * refusal has any other reason (that error is shown as it is). */
function identicalPairs(e: unknown): ReviewFlag[] | null {
  if (!(e instanceof ApiError) || e.code !== "INVALID_QUESTIONS") return null;
  const errors = (e.extra.errors ?? []) as { position?: number; message?: string }[];
  const pairs = errors.map((x) => ({ match: SAME.exec(x.message ?? ""), position: x.position }));
  if (!pairs.length || pairs.some((x) => !x.match || !x.position)) return null;
  return pairs.map((x) => ({ positions: [Number(x.match![1]), x.position!],
    reason: "Identical question (same text, options and pictures): a paper may contain it only once. Remove one of them." }));
}

/** The server's dry run of each paper (POST /papers/check: the same checks as building, nothing is written); only papers with flags. */
export async function reviewPapers(scope: "GLOBAL" | "PROVIDER", branchId: string | undefined, groups: { subject_id: string; title: string; question_ids: string[] }[],
  meta: { exam_type?: string | null; target_class?: string | null } = {}): Promise<SubjectReview[]> {
  const out: SubjectReview[] = [];
  for (const g of groups) {
    let r: { review?: ReviewFlag[] };
    try {
      r = await api<{ review?: ReviewFlag[] }>("/api/v1/papers/check", { method: "POST", body: {
        scope, ...(scope === "PROVIDER" ? { branch_id: branchId } : {}), subject_id: g.subject_id, title: g.title, question_ids: g.question_ids,
        exam_type: meta.exam_type || null, target_class: meta.target_class || null } });
    } catch (e) {
      const blocking = identicalPairs(e);
      if (!blocking) throw e;
      out.push({ subject_id: g.subject_id, question_ids: g.question_ids, flags: [], blocking });
      continue;
    }
    if (r.review?.length) out.push({ subject_id: g.subject_id, question_ids: g.question_ids, flags: r.review, blocking: [] });
  }
  return out;
}

export function groupBySubject(selected: Map<string, BankQuestion>) {
  const groups = new Map<string, string[]>();
  selected.forEach((q) => groups.set(q.subject_id, [...(groups.get(q.subject_id) ?? []), q.question_id]));
  return [...groups.entries()].map(([subject_id, question_ids]) => ({ subject_id, question_ids }));
}

/** "+4 / −1", or the note that the test decides the marks of a question without marks of its own. */
export const marksText = (v: { marks_positive: string | null; marks_negative: string | null }) =>
  v.marks_positive === null ? "Set by the test" : `+${Number(v.marks_positive)} / −${Number(v.marks_negative)}`;
