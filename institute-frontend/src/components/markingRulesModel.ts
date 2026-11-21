/**
 * Rules-based marking (marking mode RULES): the test creator types the marks for a correct answer and the marks deducted for a
 * wrong answer. One rule may apply to every subject of the test, and a subject may have its own rule that overrides it. There are no
 * presets and no default values. Unanswered questions always score 0.
 *
 * Marks are decimals with at most 2 decimal places (0.25, 1.5, 4). They are kept as the typed TEXT and sent as text, so they never
 * pass through floating-point numbers; totals shown here are computed in hundredths (whole numbers) for the same reason.
 * The server validates everything again (institute_common.core.marking); these checks only give early, friendly messages.
 */

/** The default question type of a rule (every rule made before MSQ and NAT existed). MSQ / NAT rows are added per type; an MSQ is
 * all-or-nothing and never loses marks, so its "wrong" is always 0 (the server refuses anything else). */
export const QUESTION_TYPE = "MCQ_SINGLE";
export const RULE_TYPES = ["MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"] as const;
export const RULE_TYPE_LABEL: Record<string, string> = { MCQ_SINGLE: "MCQ", MCQ_MULTI: "MSQ", NUMERICAL: "NAT" };

/** One row of the editor. subject_id null = "every subject of the test". Marks are the text the user typed. question_type absent: MCQ. */
export type RuleRow = { subject_id: string | null; correct: string; wrong: string; question_type?: string };
const typeOf = (r: { question_type?: string }) => r.question_type ?? QUESTION_TYPE;

/** A rule as the API stores and returns it (marks are exact decimal strings). */
export type MarkingRule = { subject_id: string | null; question_type: string; correct: string; wrong: string };

/** A new, empty editor: one rule for every subject with no marks filled in (nothing is pre-filled on purpose). */
export const emptyRules = (): RuleRow[] => [{ subject_id: null, correct: "", wrong: "" }];

const MARK = /^\d{1,3}(\.\d{1,2})?$/;          // 0 .. 999.99, at most 2 decimal places (no sign, no exponent)

/** A typed mark in hundredths (1.25 -> 125), or null when it is not a valid mark. Whole-number maths avoids float rounding. */
export function toHundredths(text: string): number | null {
  const t = text.trim();
  if (!MARK.test(t)) return null;
  const [whole, fraction = ""] = t.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

/** Hundredths back to a short decimal text: 125 -> "1.25", 150 -> "1.5", 400 -> "4". */
export function fromHundredths(value: number): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const fraction = String(abs % 100).padStart(2, "0").replace(/0+$/, "");
  return `${sign}${Math.floor(abs / 100)}${fraction ? `.${fraction}` : ""}`;
}

/** Why the rules cannot be used for a test with these subjects, or null when they are complete and valid. */
export function rulesProblem(rows: RuleRow[], subjectIds: string[]): string | null {
  const used = rows.filter((r) => r.subject_id !== null || r.correct.trim() || r.wrong.trim());
  if (used.length === 0) return "Enter the marks for a correct and for a wrong answer.";
  for (const r of used) {
    const correct = toHundredths(r.correct);
    const wrong = toHundredths(r.wrong);
    if (correct === null || wrong === null) return "Marks must be numbers with at most 2 decimal places, e.g. 4, 1.5 or 0.25.";
    if (correct <= 0 || correct > 10000 || wrong > 10000) return "Marks for a correct answer must be more than 0; marks may be at most 100.";
  }
  if (used.some((r) => typeOf(r) === "MCQ_MULTI" && toHundredths(r.wrong) !== 0)) return "MSQ questions never lose marks: their wrong marks must be 0.";
  for (const type of new Set(used.map(typeOf))) {          // every type that has rules must cover every subject (as the server checks)
    const mine = used.filter((r) => typeOf(r) === type);
    const hasGeneral = mine.some((r) => r.subject_id === null);
    const missing = subjectIds.filter((id) => !hasGeneral && !mine.some((r) => r.subject_id === id));
    if (missing.length) {
      return `Every subject needs ${type === QUESTION_TYPE ? "" : `${RULE_TYPE_LABEL[type]} `}marks: fill in the rule for all subjects or add a rule for each subject.`;
    }
  }
  return null;
}

/** The request body field `marking_rules` (marks sent as the exact text typed). */
export function rulesPayload(rows: RuleRow[]) {
  return {
    rules: rows
      .filter((r) => r.subject_id !== null || r.correct.trim() || r.wrong.trim())
      .map((r) => ({ subject_id: r.subject_id, question_type: typeOf(r), correct: r.correct.trim(), wrong: r.wrong.trim() })),
  };
}

/** The MCQ rule that marks a subject's questions: its own rule first, otherwise the rule for every subject.
 * shortcut: totals here use the MCQ rule (the page does not know each paper's MSQ / NAT counts); the server computes the real total at generation. */
function ruleFor<T extends { subject_id: string | null; question_type?: string }>(rows: T[], subjectId: string): T | undefined {
  const mcq = rows.filter((r) => typeOf(r) === QUESTION_TYPE);
  return mcq.find((r) => r.subject_id === subjectId) ?? mcq.find((r) => r.subject_id === null);
}

/** Maximum marks of `questions` questions of one subject, as text ("—" when the rules do not cover it yet). */
export function subjectTotal(rows: RuleRow[], subjectId: string, questions: number): string {
  const correct = toHundredths(ruleFor(rows, subjectId)?.correct ?? "");
  return correct === null ? "—" : fromHundredths(correct * questions);
}

/** Maximum marks of the whole test from its subjects' question counts, as text ("—" when some subject is not covered). */
export function rulesTotal(rows: RuleRow[], sections: { subject_id: string; questions: number }[]): string {
  let total = 0;
  for (const s of sections) {
    const correct = toHundredths(ruleFor(rows, s.subject_id)?.correct ?? "");
    if (correct === null) return "—";
    total += correct * s.questions;
  }
  return fromHundredths(total);
}

/** One line per rule for review screens and summaries, e.g. "All subjects: +1.25 correct, −0.25 wrong". */
export function describeRules(rules: MarkingRule[] | RuleRow[], subjectName: (id: string) => string): string[] {
  return rules.map((r) => `${typeOf(r) === QUESTION_TYPE ? "" : `${RULE_TYPE_LABEL[typeOf(r)]} · `}${r.subject_id === null ? "All subjects" : subjectName(r.subject_id)}: +${r.correct} correct, −${r.wrong || "0"} wrong`);
}

/** One-line description of any test's marking for summaries (PER_QUESTION, UNIFORM or RULES). `subjectName` names subject rules
 *  where the page knows the subjects; otherwise they read "One subject". */
export function markingSummary(t: { marking_mode: string; marks_correct?: number | null; marks_wrong?: number | null; marking_rules?: { rules: MarkingRule[] } | null },
  subjectName: (id: string) => string = () => "One subject"): string {
  if (t.marking_mode === "UNIFORM") return `+${t.marks_correct} correct, −${t.marks_wrong} wrong, 0 unanswered`;
  if (t.marking_mode === "RULES" && t.marking_rules) return `${describeRules(t.marking_rules.rules, subjectName).join("; ")}; 0 unanswered`;
  return "Each question's own marks, 0 unanswered";
}

/** The same description from a marking snapshot as the API returns it ({mode, correct, wrong} or {mode: "RULES", rules}). */
export const snapshotSummary = (m: { mode: string; correct?: number | null; wrong?: number | null; rules?: MarkingRule[] } | null | undefined) =>
  markingSummary({ marking_mode: m?.mode ?? "PER_QUESTION", marks_correct: m?.correct, marks_wrong: m?.wrong, marking_rules: m?.rules ? { rules: m.rules } : null });
