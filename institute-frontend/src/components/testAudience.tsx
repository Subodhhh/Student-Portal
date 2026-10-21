import { useState } from "react";
import { Alert, SearchField, Spinner, StatusBadge } from "./ui";
import { useApi } from "../hooks/useApi";
import type { AudienceState, ScopeResponse, TestPaper } from "../services/createTest";

// Shared by the Daily Test and the Weekly Test: who takes the test, and which approved paper a subject uses.
// The server decides every rule (scope, eligibility, paper availability); these components only collect the choice.

export function AudienceFields({ providerId, scope, value, onChange, studentsPath = "/api/v1/tests/students" }: {
  providerId: string; scope: ScopeResponse; value: AudienceState; onChange: (a: AudienceState) => void; studentsPath?: string;
}) {
  const chosen = scope.branches.filter((b) => value.branchIds.includes(b.branch_id));
  const flip = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  const toggleBranch = (id: string) => {
    if (!value.branchIds.includes(id)) return onChange({ ...value, branchIds: [...value.branchIds, id] });
    const gone = scope.branches.find((b) => b.branch_id === id);
    // students were chosen for the previous branch set: choose again
    onChange({ branchIds: value.branchIds.filter((b) => b !== id), wholeIds: value.wholeIds.filter((b) => b !== id),
      batchIds: value.batchIds.filter((b) => !gone?.batches.some((x) => x.batch_id === b)), students: new Map() });
  };
  return (
    <>
      <fieldset><legend>Branches</legend>
        <div className="check-grid">{scope.branches.map((b) => (
          <label key={b.branch_id}><input type="checkbox" checked={value.branchIds.includes(b.branch_id)} onChange={() => toggleBranch(b.branch_id)} /> {b.name}</label>
        ))}</div>
      </fieldset>
      {chosen.map((b) => (
        <fieldset key={b.branch_id}><legend>{b.name}</legend>
          {b.whole_branch_allowed && <label className="row checkbox-field"><input type="checkbox" checked={value.wholeIds.includes(b.branch_id)}
            onChange={() => onChange({ ...value, wholeIds: flip(value.wholeIds, b.branch_id) })} /> Every student of {b.name}</label>}
          {!value.wholeIds.includes(b.branch_id) && (b.batches.length === 0 ? <p className="muted small">No batches available to you here.</p> : (
            <div className="check-grid">{b.batches.map((x) => (
              <label key={x.batch_id}><input type="checkbox" checked={value.batchIds.includes(x.batch_id)} onChange={() => onChange({ ...value, batchIds: flip(value.batchIds, x.batch_id) })} /> {x.name}</label>
            ))}</div>
          ))}
        </fieldset>
      ))}
      {value.branchIds.length > 0 && <StudentPicker providerId={providerId} branchIds={value.branchIds} selected={value.students} path={studentsPath}
        onChange={(students) => onChange({ ...value, students })} />}
    </>
  );
}

function StudentPicker({ providerId, branchIds, selected, onChange, path }: {
  providerId: string; branchIds: string[]; selected: Map<string, string>; onChange: (m: Map<string, string>) => void; path: string;
}) {
  const [q, setQ] = useState("");
  const qs = new URLSearchParams({ provider_id: providerId });
  branchIds.forEach((b) => qs.append("branch_ids", b));
  if (q.trim()) qs.set("q", q.trim());
  const list = useApi<{ items: { student_id: string; name: string | null; branches: string[]; batches: string[] }[]; total: number }>(`${path}?${qs.toString()}`);
  const label = (s: { student_id: string; name: string | null; branches: string[]; batches: string[] }) =>
    `${s.name ?? "Name unavailable"} · ${s.student_id} · ${s.branches.join(", ")}${s.batches.length ? ` (${s.batches.join(", ")})` : ""}`;
  return (
    <fieldset><legend>Individual students (optional)</legend>
      <SearchField label="Find students by name or Student ID" value={q} onChange={setQ} />
      {list.error && <Alert kind="error">{list.error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <div className="check-grid">{(list.data?.items ?? []).slice(0, 50).map((s) => (
          <label key={s.student_id}><input type="checkbox" aria-label={`Select ${s.student_id}`} checked={selected.has(s.student_id)} onChange={() => {
            const m = new Map(selected);
            if (m.has(s.student_id)) m.delete(s.student_id); else m.set(s.student_id, `${s.name ?? s.student_id} (${s.student_id})`);
            onChange(m);
          }} /> {label(s)}</label>
        ))}</div>
      )}
      {selected.size > 0 && <p className="small">{selected.size} student(s) selected individually.</p>}
    </fieldset>
  );
}

/** Approved papers of one subject the caller may use (own, shared with the provider, or unreserved platform papers). */
export function PaperPicker({ providerId, subjectId, label, value, onChange, needPublished }: {
  providerId: string; subjectId: string; label: string; value: string; onChange: (paper: TestPaper | null) => void; needPublished: boolean;
}) {
  const list = useApi<{ items: TestPaper[] }>(`/api/v1/tests/papers?provider_id=${providerId}&subject_id=${subjectId}`);
  const items = (list.data?.items ?? []).filter((p) => !needPublished || p.published_version);
  const current = items.find((p) => p.paper_id === value);
  if (list.error) return <Alert kind="error">{list.error}</Alert>;
  if (list.loading && !list.data) return <Spinner />;
  return (
    <div className="field">
      <label htmlFor={`paper-pick-${subjectId}`}>{label}</label>
      <select id={`paper-pick-${subjectId}`} value={value} onChange={(e) => onChange(items.find((p) => p.paper_id === e.target.value) ?? null)}>
        <option value="">{items.length ? "Choose a paper" : "No approved paper available"}</option>
        {items.map((p) => (
          <option key={p.paper_id} value={p.paper_id}>
            {p.title} · {p.source}{p.published_version ? ` · v${p.published_version.version_no}, ${p.published_version.question_count} questions` : " · no published version yet"}
          </option>
        ))}
      </select>
      {current && !current.published_version && <p className="muted small"><StatusBadge status="NOT_PUBLISHED" /> Tests generated before a version is approved leave this subject out and notify the responsible staff.</p>}
    </div>
  );
}
