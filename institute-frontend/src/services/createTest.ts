import { api } from "./api";

// Types and pure helpers of the Daily Test wizard and the Weekly Test form (kept out of the page modules for fast refresh).

export type StepId = "audience" | "name" | "subjects" | "details" | "files" | "validate" | "review";
export const STEPS: { id: StepId; label: string }[] = [
  { id: "audience", label: "Audience" }, { id: "name", label: "Test name" }, { id: "subjects", label: "Subjects" },
  { id: "details", label: "Details & schedule" }, { id: "files", label: "Question papers" },
  { id: "validate", label: "Import & validate" }, { id: "review", label: "Review & publish" },
];

export type ScopeResponse = {
  provider_id: string; provider_name: string;
  branches: { branch_id: string; name: string; whole_branch_allowed: boolean; batches: { batch_id: string; name: string }[]; subjects: { subject_id: string; name: string }[] }[];
};
export type CheckError = { field: string; value: string | null; message: string };
export type UploadError = { row: number | null; field: string | null; message: string; question?: string };
export type SubjectReport = {
  ok: boolean; subject_id: string; message?: string; errors: UploadError[]; reused?: boolean; fromBank?: boolean;
  paper_version_id?: string; question_count?: number; total_marks?: string;
  summary: { questions_detected: number; questions_valid: number; questions_failed: number; threshold_exceeded: boolean; critical: boolean } | null;
};
export type TestType = "DAILY" | "WEEKLY" | "GLOBAL";
export const TEST_TYPE_LABEL: Record<TestType, string> = { DAILY: "Daily", WEEKLY: "Weekly", GLOBAL: "Global" };
export type TestPaper = {
  paper_id: string; title: string; scope: string; source: "Own" | "Shared" | "Platform";
  published_version: { paper_version_id: string; version_no: number; question_count: number; total_marks: string } | null;
};
export type ScheduledTest = {
  plan_id: string; test_type: TestType; series_id: string | null; global_test_id: string | null; exam_id: string | null; exam_version_id: string | null; name: string; state: string; exam_status: string | null; delivery_status: string | null;
  error: string | null; provider_name: string | null; home_branch: string | null; start_at: string; end_at: string; duration_minutes: number; subjects: string[];
  audience: { type: string; branches: string[]; batches: string[]; students: number }; assigned_students: number; question_count: number | null; total_marks: string | null;
  marking: { mode: string; correct?: number; wrong?: number } | null; created_at: string;
};

// ---- audience selection shared by the Daily and Weekly Test pages (components/testAudience.tsx renders it)
export type AudienceState = { branchIds: string[]; wholeIds: string[]; batchIds: string[]; students: Map<string, string> };
export const emptyAudience = (): AudienceState => ({ branchIds: [], wholeIds: [], batchIds: [], students: new Map() });

/** Subjects available in EVERY selected branch. */
export function subjectsInAll(scope: ScopeResponse, branchIds: string[]) {
  const chosen = scope.branches.filter((b) => branchIds.includes(b.branch_id));
  return chosen.length === 0 ? [] : chosen[0].subjects.filter((s) => chosen.every((b) => b.subjects.some((x) => x.subject_id === s.subject_id)));
}

export function audienceReady(a: AudienceState): string | null {
  if (a.branchIds.length === 0) return "Select at least one branch.";
  return a.wholeIds.length || a.batchIds.length || a.students.size ? null : "Select an entire branch, a batch or a student.";
}

export function audienceBody(providerId: string, branchIds: string[], wholeIds: string[], batchIds: string[], studentIds: string[]) {
  return { provider_id: providerId, branch_ids: branchIds, whole_branch_ids: wholeIds.filter((b) => branchIds.includes(b)), batch_ids: batchIds, student_ids: studentIds };
}

/** datetime-local value (the user's timezone) -> ISO 8601 in UTC with an explicit offset, as the API requires. */
export const localToIso = (v: string) => (v ? new Date(v).toISOString() : "");

export function draftBody(d: {
  audience: ReturnType<typeof audienceBody>; name: string; description: string; subjectIds: string[];
  meta: { duration: string; start: string; end: string; marking: "UNIFORM" | "PER_QUESTION"; correct: string; wrong: string; exam_type: string; target_class: string };
  versions: Record<string, string | null>;
}) {
  const uniform = d.meta.marking === "UNIFORM";
  return {
    audience: d.audience, name: d.name, description: d.description.trim() || null,
    subjects: d.subjectIds.map((sid) => ({ subject_id: sid, paper_version_id: d.versions[sid] ?? null })),
    duration_minutes: Number(d.meta.duration), start_at: localToIso(d.meta.start), end_at: localToIso(d.meta.end), marking_mode: d.meta.marking,
    marks_correct: uniform ? Number(d.meta.correct) : null, marks_wrong: uniform ? Number(d.meta.wrong) : null,
    exam_type: d.meta.exam_type.trim() || null, target_class: d.meta.target_class.trim() || null,
  };
}

export const newRequestKey = () => (crypto.randomUUID ? crypto.randomUUID() : `ct-${Date.now()}-${Math.random().toString(36).slice(2)}`);

export async function summarize(subjectId: string, versionId: string, reused: boolean): Promise<SubjectReport> {
  const pv = await api<{ latest_version: { question_count: number; total_marks: string } }>(`/api/v1/paper-versions/${versionId}`);
  return { ok: true, subject_id: subjectId, paper_version_id: versionId, question_count: pv.latest_version.question_count,
    total_marks: pv.latest_version.total_marks, reused, errors: [], summary: null };
}

const cell = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;

export function reportCsv(rows: { name: string; report?: SubjectReport }[]) {
  const out = ["subject,result,questions_detected,questions_valid,questions_failed,question,row,field,problem"];
  for (const { name, report } of rows) {
    if (!report) { out.push([name, "NOT VALIDATED", "", "", "", "", "", "", ""].map(cell).join(",")); continue; }
    const s = report.summary;
    const base = [name, report.ok ? "PASSED" : "REJECTED", s?.questions_detected ?? report.question_count ?? "", s?.questions_valid ?? report.question_count ?? "", s?.questions_failed ?? 0];
    if (report.errors.length === 0) out.push([...base, "", "", "", report.ok ? "" : report.message ?? ""].map(cell).join(","));
    for (const e of report.errors) out.push([...base, e.question ?? "", e.row ?? "", e.field ?? "", e.message].map(cell).join(","));
  }
  return out.join("\n") + "\n";
}
