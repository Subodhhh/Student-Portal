import { Field, SelectField } from "./ui";
import { type AcademicForm, CLASS_LEVELS, academicErrors } from "../services/classLevels";

/** Class + (Other description) + Academic Year inputs. `errors` are shown only once `showErrors` is set (after a save attempt). */
export function AcademicFields({ value, onChange, required, showErrors, disabled, labels = { cls: "Class / level", year: "Academic year" } }: {
  value: AcademicForm; onChange: (v: AcademicForm) => void; required: boolean; showErrors?: boolean; disabled?: boolean; labels?: { cls: string; year: string };
}) {
  const e = showErrors ? academicErrors(value, required) : {};
  return (
    <div className="grid cols-2">
      <SelectField label={labels.cls} value={value.class_level} disabled={disabled} error={e.class_level} required={required}
        onChange={(ev) => onChange({ ...value, class_level: ev.target.value, other_class_description: ev.target.value === "OTHER" ? value.other_class_description : "" })}>
        <option value="">{required ? "Select…" : "No default"}</option>
        {CLASS_LEVELS.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
      </SelectField>
      <Field label={labels.year} value={value.academic_year} disabled={disabled} error={e.academic_year} required={required} placeholder="2026-2027" inputMode="numeric"
        onChange={(ev) => onChange({ ...value, academic_year: ev.target.value })} />
      {value.class_level === "OTHER" && (
        <Field label="Describe the class" value={value.other_class_description} disabled={disabled} error={e.other_class_description} required maxLength={120}
          onChange={(ev) => onChange({ ...value, other_class_description: ev.target.value })} />
      )}
    </div>
  );
}

/** The Class a test is for (its `target_class`): any Class except Other, or none. A stored legacy label is shown so it can be replaced. */
export function ClassTargetSelect({ value, onChange, label = "Class / level (optional)", disabled }: {
  value: string; onChange: (label: string) => void; label?: string; disabled?: boolean;
}) {
  const known = CLASS_LEVELS.some((c) => c.label === value);
  return (
    <SelectField label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}
      error={value && !known ? "Not a recognised class: choose one (students are matched by Class)." : undefined}>
      <option value="">Any class</option>
      {value && !known && <option value={value}>{value} (not recognised)</option>}
      {CLASS_LEVELS.filter((c) => c.code !== "OTHER").map((c) => <option key={c.code} value={c.label}>{c.label}</option>)}
    </SelectField>
  );
}
