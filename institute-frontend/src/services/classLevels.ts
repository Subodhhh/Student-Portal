/** Class / Academic Level: the same controlled values as the backend (institute_common/core/class_levels.py). Stored as codes. */
export const CLASS_LEVELS = [
  { code: "6TH", label: "6th" }, { code: "7TH", label: "7th" }, { code: "8TH", label: "8th" }, { code: "9TH", label: "9th" },
  { code: "10TH", label: "10th" }, { code: "11TH", label: "11th" }, { code: "12TH", label: "12th" },
  { code: "DROPPER", label: "Dropper" }, { code: "OTHER", label: "Other" },
] as const;
const LABEL: Record<string, string> = Object.fromEntries(CLASS_LEVELS.map((c) => [c.code, c.label]));

/** An enrollment's Class / Academic Year as the API returns it (null: an enrollment made before Classes existed). */
export type Academic = { class_level: string; class_label: string; academic_year: string; other_class_description: string | null };
/** Form state: codes; "" = not chosen. */
export type AcademicForm = { class_level: string; academic_year: string; other_class_description: string };
export const EMPTY_ACADEMIC: AcademicForm = { class_level: "", academic_year: "", other_class_description: "" };

export function classLabel(code: string | null | undefined): string {
  return code ? (LABEL[code] ?? code) : "";
}

/** "11th · 2026-2027", "Other (NIOS) · 2026-2027"; "" when not set. */
export function academicText(a: Academic | null | undefined): string {
  if (!a) return "";
  return `${a.class_label}${a.other_class_description ? ` (${a.other_class_description})` : ""} · ${a.academic_year}`;
}

/** "2026-2027": two consecutive years (2000-2099), as the API requires. */
export function yearError(v: string): string | null {
  const m = /^(\d{4})-(\d{4})$/.exec(v.trim());
  if (!m) return "Use the form 2026-2027.";
  const a = Number(m[1]);
  return Number(m[2]) === a + 1 && a >= 2000 && a <= 2099 ? null : "Use two consecutive years, e.g. 2026-2027.";
}

/** Problems with a form; `required` = Class and Academic Year must be given (enrollments), otherwise both are optional (batch defaults). */
export function academicErrors(f: AcademicForm, required: boolean): Partial<Record<keyof AcademicForm, string>> {
  const e: Partial<Record<keyof AcademicForm, string>> = {};
  if (required && !f.class_level) e.class_level = "Choose the class.";
  if (f.class_level === "OTHER" && !f.other_class_description.trim()) e.other_class_description = "Describe the class.";
  const year = f.academic_year.trim();
  const yearProblem = year ? yearError(year) : required ? "Enter the academic year." : null;
  if (yearProblem) e.academic_year = yearProblem;
  return e;
}

/** API body for a form (codes; the description only for Other). */
export function academicBody(f: AcademicForm): { class_level: string; academic_year: string; other_class_description: string | null } {
  return { class_level: f.class_level, academic_year: f.academic_year.trim(), other_class_description: f.class_level === "OTHER" ? f.other_class_description.trim() : null };
}

export function toForm(a: Academic | null | undefined): AcademicForm {
  return a ? { class_level: a.class_level, academic_year: a.academic_year, other_class_description: a.other_class_description ?? "" } : EMPTY_ACADEMIC;
}
