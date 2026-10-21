import type { GlobalTest } from "../components/globalTests";

// Types of the centrally scheduled Global Tests (dates published by the platform, papers submitted by providers).

export const subjectsOf = (t: GlobalTest) => (t.kind === "SCHEDULE" ? t.subjects : t.sections).map((s) => s.subject ?? "Subject").join(", ");
export const markingOf = (t: Pick<GlobalTest, "marking_mode" | "marks_correct" | "marks_wrong">) =>
  t.marking_mode === "UNIFORM" ? `+${t.marks_correct} correct, −${t.marks_wrong} wrong` : "Each paper's own marks";

export type OccurrenceSubject = { subject_id: string; subject: string | null; can_submit: boolean };
export type Occurrence = {
  occurrence_id: string; test_id: string; test_name: string; test_status: string; exam_type: string | null; target_class: string | null;
  duration_minutes: number; marking: { mode: string; correct: number | null; wrong: number | null }; start_at: string; end_at: string;
  status: "DRAFT" | "PUBLISHED" | "CANCELLED"; submission_deadline: string; submissions_open: boolean; generated_at: string | null;
  generation_error: string | null; cancel_reason: string | null; subjects: OccurrenceSubject[]; submissions: { active: number; withdrawn: number };
};
export type Submission = {
  submission_id: string; subject_id: string; subject: string | null; provider_id: string; provider_name: string | null; branch_name: string | null;
  status: "ACTIVE" | "WITHDRAWN"; paper_title: string | null; paper_version_id: string; version_no: number | null; version_status: string | null;
  ready: boolean; question_count: number | null; audience: { entire_branches: (string | null)[]; batches: string[]; students: number };
  students_now: number; submitted_by_name: string | null; submitted_at: string; withdraw_reason: string | null; can_withdraw: boolean;
};
export type CoverageRow = { provider_id: string; provider_name: string | null; subject_id: string; subject: string; submissions: number; ready: number;
  students_covered: number; ambiguous: number; missing: boolean };
export type OccurrenceDetail = Occurrence & { submission_list: Submission[]; coverage: CoverageRow[];
  exams?: { exam_id: string; name: string; status: string; assigned: number }[] };
export type SubmittablePaper = { paper_id: string; title: string; paper_version_id: string; version_no: number; version_status: string; ready: boolean; question_count: number };

export const fmtDate = (v: string) => new Date(v).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
export const audienceText = (a: Submission["audience"]) =>
  [a.entire_branches.length ? `entire branch: ${a.entire_branches.join(", ")}` : "", a.batches.length ? `batches: ${a.batches.join(", ")}` : "",
    a.students ? `${a.students} student(s) listed` : ""].filter(Boolean).join(" · ") || "—";
