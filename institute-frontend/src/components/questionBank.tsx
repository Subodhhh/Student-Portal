import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isGlobalRole } from "../auth/roles";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { PATTERN_LABEL, buildPapers, groupBySubject, type BankQuestion, type BuiltPaper, type QuestionVersion, type Source } from "../services/questions";
import { Alert, Button, DataTable, EmptyState, Field, Modal, Pagination, SearchField, SelectField, Spinner, StatusBadge, TextAreaField } from "./ui";

type Subject = { subject_id: string; code: string; name: string };
type Facets = { exam_type: string[]; target_class: string[]; chapter: string[]; topic: string[]; subtopic: string[]; difficulty: string[]; pattern: string[] };

const DIFFICULTY_LABEL: Record<string, string> = { EASY: "Easy", MEDIUM: "Medium", HARD: "Hard" };
const SUGGESTED_EXAM_TYPES = ["NEET", "AIIMS", "JEE Main", "JEE Advanced", "CUET", "Board"];

const shorten = (s: string, n = 110) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** The bank's filters and results. `selectable` adds checkboxes (used to build papers, also inside the test wizard). */
export function QuestionBrowser({ source, branchId, selected, onToggle, selectable, onChanged }: {
  source: Source; branchId?: string; selected?: Map<string, BankQuestion>; onToggle?: (q: BankQuestion) => void; selectable?: boolean; onChanged?: () => void;
}) {
  const { me } = useAuth();
  const platform = isGlobalRole(me?.primary_role) || !!me?.assignments.some((a) => isGlobalRole(a.role));
  const [f, setF] = useState({ q: "", subject_id: "", exam_type: "", target_class: "", chapter: "", topic: "", subtopic: "", difficulty: "", pattern: "", status: "ACTIVE" });
  const [includeUsed, setIncludeUsed] = useState(true);
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<string | null>(null);
  const [editing, setEditing] = useState<BankQuestion | null>(null);
  const [versioning, setVersioning] = useState<BankQuestion | null>(null);
  const act = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF((x) => ({ ...x, [k]: e.target.value })); setPage(1); };
  const base = new URLSearchParams({ scope: source });
  if (branchId && source !== "GLOBAL") base.set("branch_id", branchId);
  const facetParams = new URLSearchParams(base);
  if (f.subject_id) facetParams.set("subject_id", f.subject_id);
  const params = new URLSearchParams(base);
  Object.entries(f).forEach(([k, v]) => { if (v && k !== "q") params.set(k, v); });
  if (f.q.trim().length >= 2) params.set("q", f.q.trim());
  if (!includeUsed) params.set("include_used", "false");
  params.set("page", String(page));
  params.set("page_size", "20");
  const subjects = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  const facets = useApi<Facets>(`/api/v1/question-taxonomy?${facetParams.toString()}`);
  const list = useApi<Page<BankQuestion>>(`/api/v1/questions?${params.toString()}`);
  const sName = (id: string) => subjects.data?.items.find((s) => s.subject_id === id)?.name ?? "Subject";
  const fa = facets.data ?? undefined;
  const opts = (values: string[] | undefined, label?: (v: string) => string) => (values ?? []).map((v) => <option key={v} value={v}>{label ? label(v) : v}</option>);
  const archive = (q: BankQuestion) => act.run(async () => {
    await api(`/api/v1/questions/${q.question_id}`, { method: "PATCH", body: { status: q.status === "ACTIVE" ? "ARCHIVED" : "ACTIVE" } });
    list.reload();
    onChanged?.();
  });

  return (
    <div className="stack">
      <div className="filter-grid" role="group" aria-label="Question filters">
        <SearchField label="Search question text" value={f.q} onChange={(v) => { setF((x) => ({ ...x, q: v })); setPage(1); }} />
        <SelectField label="Exam type" value={f.exam_type} onChange={set("exam_type")}><option value="">Any</option>{opts(fa?.exam_type)}</SelectField>
        <SelectField label="Class" value={f.target_class} onChange={set("target_class")}><option value="">Any</option>{opts(fa?.target_class)}</SelectField>
        <SelectField label="Subject" value={f.subject_id} onChange={(e) => { setF((x) => ({ ...x, subject_id: e.target.value, chapter: "", topic: "", subtopic: "" })); setPage(1); }}>
          <option value="">Any</option>{(subjects.data?.items ?? []).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
        </SelectField>
        <SelectField label="Chapter" value={f.chapter} onChange={set("chapter")}><option value="">Any</option>{opts(fa?.chapter)}</SelectField>
        <SelectField label="Topic" value={f.topic} onChange={set("topic")}><option value="">Any</option>{opts(fa?.topic)}</SelectField>
        <SelectField label="Subtopic" value={f.subtopic} onChange={set("subtopic")}><option value="">Any</option>{opts(fa?.subtopic)}</SelectField>
        <SelectField label="Difficulty" value={f.difficulty} onChange={set("difficulty")}><option value="">Any</option>{opts(fa?.difficulty, (v) => DIFFICULTY_LABEL[v] ?? v)}</SelectField>
        <SelectField label="Pattern" value={f.pattern} onChange={set("pattern")}><option value="">Any</option>{opts(fa?.pattern, (v) => PATTERN_LABEL[v] ?? v)}</SelectField>
        <SelectField label="Status" value={f.status} onChange={set("status")}><option value="ACTIVE">Active</option><option value="ARCHIVED">Archived</option><option value="ALL">All</option></SelectField>
        <label className="row checkbox-field"><input type="checkbox" checked={includeUsed} onChange={(e) => { setIncludeUsed(e.target.checked); setPage(1); }} /> Include questions already used in papers</label>
      </div>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      {list.loading && !list.data ? <Spinner label="Loading questions" /> : (
        <>
          <p className="muted small" aria-live="polite">{list.data?.total ?? 0} question(s) match.{selectable && selected ? ` ${selected.size} selected.` : ""}</p>
          <DataTable rows={list.data?.items ?? []} rowKey={(q) => q.question_id}
            empty={<EmptyState title="No questions match these filters" hint="Change the filters, or add questions to the bank." />}
            columns={[
              ...(selectable ? [{ header: "Select", render: (q: BankQuestion) => (
                <input type="checkbox" aria-label={`Select question: ${shorten(q.latest.stem, 60)}`} checked={!!selected?.has(q.question_id)}
                  disabled={q.status !== "ACTIVE" || (!platform && !!q.reserved_for_global_test)}
                  title={!platform && q.reserved_for_global_test ? "Part of a Global Test: schedule that test instead" : undefined} onChange={() => onToggle?.(q)} />
              ) }] : []),
              { header: "Question", render: (q) => <button className="link-btn left" onClick={() => setPreview(q.question_id)}>{shorten(q.latest.stem)}</button> },
              { header: "Subject", render: (q) => sName(q.subject_id) },
              { header: "Chapter / topic", render: (q) => [q.chapter, q.topic, q.subtopic].filter(Boolean).join(" › ") || "—" },
              { header: "Exam / class", render: (q) => [q.exam_type, q.target_class].filter(Boolean).join(" · ") || "—" },
              { header: "Difficulty", render: (q) => (q.difficulty ? DIFFICULTY_LABEL[q.difficulty] : "—") },
              { header: "Marks", render: (q) => `+${Number(q.latest.marks_positive)} / −${Number(q.latest.marks_negative)}` },
              { header: "Source", render: (q) => <StatusBadge status={q.scope === "GLOBAL" ? "GLOBAL" : "PROVIDER"} /> },
              { header: "Used", render: (q) => q.usage_count },
              { header: "Actions", render: (q) => (
                <div className="row nowrap">
                  <Button small variant="secondary" onClick={() => setPreview(q.question_id)}>Preview</Button>
                  {q.can_edit && <Button small variant="secondary" onClick={() => setEditing(q)}>Classify</Button>}
                  {q.can_edit && q.status === "ACTIVE" && <Button small variant="secondary" onClick={() => setVersioning(q)}>New version</Button>}
                  {q.can_edit && <Button small variant={q.status === "ACTIVE" ? "danger" : "secondary"} busy={act.busy} onClick={() => void archive(q)}>{q.status === "ACTIVE" ? "Archive" : "Restore"}</Button>}
                </div>
              ) },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
      {preview && <QuestionPreview questionId={preview} subjectName={sName} onClose={() => setPreview(null)} />}
      {editing && <ClassifyQuestion question={editing} facets={fa} onClose={() => setEditing(null)} onDone={() => { setEditing(null); list.reload(); onChanged?.(); }} />}
      {versioning && <NewQuestionVersion question={versioning} onClose={() => setVersioning(null)} onDone={() => { setVersioning(null); list.reload(); onChanged?.(); }} />}
    </div>
  );
}

function QuestionPreview({ questionId, subjectName, onClose }: { questionId: string; subjectName: (id: string) => string; onClose: () => void }) {
  const d = useApi<{ question: BankQuestion; versions: QuestionVersion[]; usage: { paper_id: string; title: string; paper_version_id: string; version_no: number; status: string; question_version_no: number }[]; usage_elsewhere: number }>(
    `/api/v1/questions/${questionId}`);
  const data = d.data;
  const q = data?.question;
  const v = q?.latest;
  return (
    <Modal title="Question preview" onClose={onClose}>
      {d.loading && !data ? <Spinner /> : d.error ? <Alert kind="error">{d.error}</Alert> : data && q && v && (
        <div className="stack">
          <p className="muted small">
            {subjectName(q.subject_id)} · {[q.exam_type, q.target_class, q.chapter, q.topic, q.subtopic].filter(Boolean).join(" › ") || "unclassified"} ·{" "}
            {PATTERN_LABEL[q.pattern] ?? q.pattern}{q.difficulty ? ` · ${DIFFICULTY_LABEL[q.difficulty]}` : ""} · {q.scope === "GLOBAL" ? "Platform question" : "Provider question"} · version {v.version_no}
          </p>
          <p className="question-stem">{v.stem}</p>
          <ol type="A" className="options">
            {[v.option_a, v.option_b, v.option_c, v.option_d].map((o, i) => (
              <li key={i} className={v.correct_option === "ABCD"[i] ? "correct" : undefined}>{o}{v.correct_option === "ABCD"[i] && <span className="sr-only"> (correct answer)</span>}</li>
            ))}
          </ol>
          <p className="small">Marks: +{Number(v.marks_positive)} for a correct answer, −{Number(v.marks_negative)} for a wrong one, 0 if unanswered.</p>
          {v.explanation && <p className="small muted">Explanation: {v.explanation}</p>}
          {v.answers_hidden && <Alert kind="info">This platform question is part of a Global Test. Its answer and explanation are hidden until your students' review window opens.</Alert>}
          {q.forked_from_question_id && <p className="small muted">Your provider's corrected copy of a question it does not own; the original is unchanged.</p>}
          <p className="small muted">Staff-only view. Students see answers and explanations only at result review, after the exam window closes.</p>
          <h3>Used in papers</h3>
          {data.usage.length === 0 && !data.usage_elsewhere ? <p className="muted small">Not used in any paper yet.</p> : (
            <ul className="plain small">
              {data.usage.map((u) => <li key={u.paper_version_id}><Link to={`/papers/${u.paper_version_id}`}>{u.title} v{u.version_no}</Link> · question version {u.question_version_no} · <StatusBadge status={u.status} /></li>)}
              {data.usage_elsewhere > 0 && <li className="muted">and {data.usage_elsewhere} paper version(s) of other providers</li>}
            </ul>
          )}
          {data.versions.length > 1 && <p className="muted small">{data.versions.length} versions exist; papers keep the exact version they were built with.</p>}
          <div className="modal-actions"><Button variant="secondary" onClick={onClose}>Close</Button></div>
        </div>
      )}
    </Modal>
  );
}

type ClassFields = { exam_type: string; target_class: string; chapter: string; topic: string; subtopic: string; difficulty: string; pattern: string; subtype: string };

function ClassificationFields({ value, onChange, facets }: { value: ClassFields; onChange: (v: ClassFields) => void; facets?: Facets }) {
  const id = useId();
  const set = (k: keyof ClassFields) => (e: { target: { value: string } }) => onChange({ ...value, [k]: e.target.value });
  const list = (k: string, values: string[]) => <datalist id={`${id}-${k}`}>{values.map((v) => <option key={v} value={v} />)}</datalist>;
  return (
    <>
      <div className="grid cols-2">
        <Field label="Exam type" value={value.exam_type} onChange={set("exam_type")} list={`${id}-exam`} placeholder="NEET" />
        <Field label="Class / target level" value={value.target_class} onChange={set("target_class")} list={`${id}-class`} placeholder="Class 12" />
        <Field label="Chapter" value={value.chapter} onChange={set("chapter")} list={`${id}-chapter`} />
        <Field label="Topic" value={value.topic} onChange={set("topic")} list={`${id}-topic`} />
        <Field label="Subtopic" value={value.subtopic} onChange={set("subtopic")} list={`${id}-subtopic`} />
        <Field label="Subtype (optional)" value={value.subtype} onChange={set("subtype")} />
        <SelectField label="Difficulty" value={value.difficulty} onChange={set("difficulty")}><option value="">Not set</option>{Object.entries(DIFFICULTY_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</SelectField>
        <SelectField label="Pattern" value={value.pattern} onChange={set("pattern")}>{Object.entries(PATTERN_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</SelectField>
      </div>
      {list("exam", [...new Set([...SUGGESTED_EXAM_TYPES, ...(facets?.exam_type ?? [])])])}
      {list("class", facets?.target_class ?? [])}{list("chapter", facets?.chapter ?? [])}{list("topic", facets?.topic ?? [])}{list("subtopic", facets?.subtopic ?? [])}
    </>
  );
}

const nullIfEmpty = (v: ClassFields) => Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x.trim() === "" ? null : x.trim()])) as Record<string, string | null>;

function ClassifyQuestion({ question, facets, onClose, onDone }: { question: BankQuestion; facets?: Facets; onClose: () => void; onDone: () => void }) {
  const [v, setV] = useState<ClassFields>({ exam_type: question.exam_type ?? "", target_class: question.target_class ?? "", chapter: question.chapter ?? "",
    topic: question.topic ?? "", subtopic: question.subtopic ?? "", difficulty: question.difficulty ?? "", pattern: question.pattern, subtype: question.subtype ?? "" });
  const { busy, error, run } = useAction();
  return (
    <Modal title="Classify question" onClose={onClose}>
      <p className="muted small">Classification helps everyone find the question. It never changes the question's content or any paper using it.</p>
      {error && <Alert kind="error">{error}</Alert>}
      <ClassificationFields value={v} onChange={setV} facets={facets} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} onClick={() => void run(async () => { await api(`/api/v1/questions/${question.question_id}`, { method: "PATCH", body: nullIfEmpty(v) }); onDone(); })}>Save</Button>
      </div>
    </Modal>
  );
}

type ContentFields = { stem: string; option_a: string; option_b: string; option_c: string; option_d: string; correct_option: string; marks_positive: string; marks_negative: string; explanation: string };

function ContentFieldsForm({ value, onChange }: { value: ContentFields; onChange: (v: ContentFields) => void }) {
  const set = (k: keyof ContentFields) => (e: { target: { value: string } }) => onChange({ ...value, [k]: e.target.value });
  return (
    <>
      <TextAreaField label="Question" value={value.stem} onChange={set("stem")} required />
      <div className="grid cols-2">
        {(["option_a", "option_b", "option_c", "option_d"] as const).map((k) => <Field key={k} label={`Option ${k.slice(-1).toUpperCase()}`} value={value[k]} onChange={set(k)} required />)}
      </div>
      <div className="grid cols-3">
        <SelectField label="Correct answer" value={value.correct_option} onChange={set("correct_option")}>{["A", "B", "C", "D"].map((o) => <option key={o}>{o}</option>)}</SelectField>
        <Field label="Marks (correct)" value={value.marks_positive} onChange={set("marks_positive")} inputMode="numeric" hint="Whole number" />
        <Field label="Negative marks (wrong)" value={value.marks_negative} onChange={set("marks_negative")} inputMode="numeric" hint="Deducted; 0 for none" />
      </div>
      <TextAreaField label="Explanation (optional, shown at result review)" value={value.explanation} onChange={set("explanation")} />
    </>
  );
}

const contentReady = (c: ContentFields) => c.stem.trim().length >= 3 && [c.option_a, c.option_b, c.option_c, c.option_d].every((o) => o.trim())
  && /^\d+$/.test(c.marks_positive) && Number(c.marks_positive) > 0 && /^\d+$/.test(c.marks_negative);
const contentBody = (c: ContentFields) => ({ ...c, explanation: c.explanation.trim() || null });

function NewQuestionVersion({ question, onClose, onDone }: { question: BankQuestion; onClose: () => void; onDone: () => void }) {
  const v = question.latest;
  const [c, setC] = useState<ContentFields>({ stem: v.stem, option_a: v.option_a, option_b: v.option_b, option_c: v.option_c, option_d: v.option_d,
    correct_option: v.correct_option ?? "A", marks_positive: String(Number(v.marks_positive)), marks_negative: String(Number(v.marks_negative)), explanation: v.explanation ?? "" });
  const { busy, error, run } = useAction();
  return (
    <Modal title={`New version of the question (now v${v.version_no})`} onClose={onClose}>
      <Alert kind="info">Saving creates version {v.version_no + 1}. Papers and scheduled exams keep the version they already use{question.usage_count ? ` (${question.usage_count} paper version(s))` : ""}.</Alert>
      {error && <Alert kind="error">{error}</Alert>}
      <ContentFieldsForm value={c} onChange={setC} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!contentReady(c)} onClick={() => void run(async () => { await api(`/api/v1/questions/${question.question_id}/versions`, { method: "POST", body: contentBody(c) }); onDone(); })}>Save as new version</Button>
      </div>
    </Modal>
  );
}

/** Add a question to the platform bank (GLOBAL, platform roles) or to a provider branch's bank. */
export function AddQuestion({ scope, branchId, providerId, onClose, onDone }: { scope: "GLOBAL" | "PROVIDER"; branchId?: string; providerId?: string; onClose: () => void; onDone: () => void }) {
  const catalogue = useApi<Page<Subject>>(scope === "GLOBAL" ? "/api/v1/subjects?page_size=100" : null);
  const offered = useApi<Subject[]>(scope === "PROVIDER" && providerId ? `/api/v1/providers/${providerId}/subjects` : null);
  const subjects = scope === "GLOBAL" ? catalogue.data?.items ?? [] : offered.data ?? [];
  const [subjectId, setSubjectId] = useState("");
  const facets = useApi<Facets>(`/api/v1/question-taxonomy?scope=${scope}${branchId && scope === "PROVIDER" ? `&branch_id=${branchId}` : ""}${subjectId ? `&subject_id=${subjectId}` : ""}`);
  const [k, setK] = useState<ClassFields>({ exam_type: "", target_class: "", chapter: "", topic: "", subtopic: "", difficulty: "", pattern: "SINGLE_CORRECT", subtype: "" });
  const [c, setC] = useState<ContentFields>({ stem: "", option_a: "", option_b: "", option_c: "", option_d: "", correct_option: "A", marks_positive: "4", marks_negative: "1", explanation: "" });
  const { busy, error, run } = useAction();
  return (
    <Modal title={scope === "GLOBAL" ? "Add a platform question" : "Add a question"} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <SelectField label="Subject" value={subjectId} onChange={(e) => setSubjectId(e.target.value)} required>
        <option value="">Select…</option>{subjects.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
      </SelectField>
      <ClassificationFields value={k} onChange={setK} facets={facets.data ?? undefined} />
      <ContentFieldsForm value={c} onChange={setC} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!subjectId || !contentReady(c)} onClick={() => void run(async () => {
          await api("/api/v1/questions", { method: "POST", body: { scope, ...(scope === "PROVIDER" ? { branch_id: branchId } : {}), subject_id: subjectId, ...nullIfEmpty(k), pattern: k.pattern, ...contentBody(c) } });
          onDone();
        })}>Add question</Button>
      </div>
    </Modal>
  );
}

export function BuildPapersDialog({ scope, branchId, selected, onClose, onDone }: { scope: "GLOBAL" | "PROVIDER"; branchId?: string; selected: Map<string, BankQuestion>;
  onClose: () => void; onDone: (papers: BuiltPaper[]) => void }) {
  const subjects = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  const groups = groupBySubject(selected);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [meta, setMeta] = useState({ exam_type: "", target_class: "" });
  const { busy, error, run } = useAction();
  const { can } = useAuth();
  const sName = (id: string) => subjects.data?.items.find((s) => s.subject_id === id)?.name ?? "Subject";
  const ready = groups.every((g) => (titles[g.subject_id] ?? "").trim().length >= 3);
  return (
    <Modal title="Create paper from selected questions" onClose={onClose}>
      <p className="muted small">{selected.size} question(s) in {groups.length} subject(s). {groups.length > 1 ? "One paper is created per subject; combine them in a test." : ""}
        {" "}The new version starts as a draft (VALIDATED){can("paper.approve") ? ", ready for you to approve and publish" : " for approval"}.</p>
      {error && <Alert kind="error">{error}</Alert>}
      {groups.map((g) => (
        <Field key={g.subject_id} label={`${sName(g.subject_id)} paper title (${g.question_ids.length} questions)`} value={titles[g.subject_id] ?? ""}
          onChange={(e) => setTitles((t) => ({ ...t, [g.subject_id]: e.target.value }))} />
      ))}
      <div className="grid cols-2">
        <Field label="Exam type (optional)" value={meta.exam_type} onChange={(e) => setMeta({ ...meta, exam_type: e.target.value })} />
        <Field label="Class (optional)" value={meta.target_class} onChange={(e) => setMeta({ ...meta, target_class: e.target.value })} />
      </div>
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!ready || groups.length === 0} onClick={() => void run(async () => {
          onDone(await buildPapers(scope, branchId, groups.map((g) => ({ ...g, title: titles[g.subject_id].trim() })), meta));
        })}>Create {groups.length > 1 ? `${groups.length} papers` : "paper"}</Button>
      </div>
    </Modal>
  );
}
