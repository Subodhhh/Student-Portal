import { api } from "./api";

// Question-bank types and helpers shared by the Question Bank page and the test-creation wizard.

export type QuestionVersion = {
  question_version_id: string; question_id: string; version_no: number; stem: string; option_a: string; option_b: string; option_c: string; option_d: string;
  correct_option: string | null; explanation: string | null; topic: string | null; marks_positive: string; marks_negative: string;
  answers_hidden?: boolean;   // a Global Test question before the provider's review point
};
export type BankQuestion = {
  question_id: string; scope: "PROVIDER" | "GLOBAL"; provider_id: string | null; branch_id: string | null; subject_id: string; status: string;
  exam_type: string | null; target_class: string | null; chapter: string | null; topic: string | null; subtopic: string | null; difficulty: string | null;
  pattern: string; subtype: string | null; question_type: string; created_at: string; latest: QuestionVersion; usage_count: number; can_edit: boolean;
  reserved_for_global_test?: boolean; forked_from_question_id?: string | null;
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

export function groupBySubject(selected: Map<string, BankQuestion>) {
  const groups = new Map<string, string[]>();
  selected.forEach((q) => groups.set(q.subject_id, [...(groups.get(q.subject_id) ?? []), q.question_id]));
  return [...groups.entries()].map(([subject_id, question_ids]) => ({ subject_id, question_ids }));
}
