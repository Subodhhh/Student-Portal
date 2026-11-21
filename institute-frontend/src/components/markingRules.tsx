import { Button, Field, SelectField } from "./ui";
import { QUESTION_TYPE, RULE_TYPES, RULE_TYPE_LABEL, type RuleRow } from "./markingRulesModel";

/** Editor of the rules: for MCQ (always) and, when added, MSQ and NAT questions - the rule for every subject, plus optional rules for
 * single subjects. MSQ is all-or-nothing: its wrong marks are fixed at 0. */
export function MarkingRulesEditor({ rows, onChange, subjects, disabled }: {
  rows: RuleRow[]; onChange: (rows: RuleRow[]) => void; subjects: { subject_id: string; name: string }[]; disabled?: boolean;
}) {
  const typeOf = (r: RuleRow) => r.question_type ?? QUESTION_TYPE;
  const update = (index: number, change: Partial<RuleRow>) => onChange(rows.map((r, i) => (i === index ? { ...r, ...change } : r)));
  const nameOf = (id: string | null) => (id === null ? "All subjects" : subjects.find((s) => s.subject_id === id)?.name ?? "Subject");
  const types = RULE_TYPES.filter((t) => t === QUESTION_TYPE || rows.some((r) => typeOf(r) === t));
  const missingTypes = RULE_TYPES.filter((t) => !types.includes(t));
  const blank = (t: string, subject_id: string | null): RuleRow => ({ subject_id, correct: "", wrong: t === "MCQ_MULTI" ? "0" : "", ...(t === QUESTION_TYPE ? {} : { question_type: t }) });
  return (
    <div className="stack">
      {types.map((t) => {
        const label = t === QUESTION_TYPE && types.length === 1 ? "" : `${RULE_TYPE_LABEL[t]} `;
        const withOwnRule = new Set(rows.filter((r) => typeOf(r) === t).map((r) => r.subject_id));
        const addable = subjects.filter((s) => !withOwnRule.has(s.subject_id));
        return (
          <div className="stack" key={t}>
            {types.length > 1 && <h4>{RULE_TYPE_LABEL[t]} questions{t === "MCQ_MULTI" ? " (all-or-nothing: a wrong or incomplete choice scores 0)" : ""}</h4>}
            {rows.map((r, i) => typeOf(r) !== t ? null : (
              <div className="grid cols-3" key={`${t}-${r.subject_id ?? "all"}`}>
                <Field label={`${label}${nameOf(r.subject_id)}: marks for a correct answer`} inputMode="decimal" placeholder="e.g. 4 or 1.25"
                  value={r.correct} disabled={disabled} onChange={(e) => update(i, { correct: e.target.value })} />
                {t === "MCQ_MULTI"
                  ? <Field label={`${label}${nameOf(r.subject_id)}: marks deducted for a wrong answer`} value="0" disabled hint="MSQ never deducts marks" />
                  : <Field label={`${label}${nameOf(r.subject_id)}: marks deducted for a wrong answer`} inputMode="decimal" placeholder="e.g. 1 or 0.25"
                    hint="0 for no negative marking" value={r.wrong} disabled={disabled} onChange={(e) => update(i, { wrong: e.target.value })} />}
                {(r.subject_id !== null || t !== QUESTION_TYPE) && !disabled && (
                  <div className="row"><Button variant="secondary" small type="button"
                    onClick={() => onChange(r.subject_id === null && t !== QUESTION_TYPE ? rows.filter((x) => typeOf(x) !== t) : rows.filter((_, j) => j !== i))}>
                    Remove {label}{r.subject_id === null ? "rules" : `${nameOf(r.subject_id)} rule`}</Button></div>
                )}
              </div>
            ))}
            {addable.length > 0 && !disabled && (
              <SelectField label={`Different ${label}marks for one subject (optional)`} value=""
                onChange={(e) => e.target.value && onChange([...rows, blank(t, e.target.value)])}>
                <option value="">Choose a subject…</option>
                {addable.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
              </SelectField>
            )}
          </div>
        );
      })}
      {!disabled && missingTypes.length > 0 && (
        <div className="row">
          {missingTypes.map((t) => <Button key={t} variant="secondary" small type="button" onClick={() => onChange([...rows, blank(t, null)])}>
            Add marks for {RULE_TYPE_LABEL[t]} questions</Button>)}
        </div>
      )}
      <p className="muted small">Unanswered questions always score 0. The rule for a subject overrides the rule for all subjects.
        {" "}Add MSQ or NAT marks only when the test's papers contain such questions.</p>
    </div>
  );
}
