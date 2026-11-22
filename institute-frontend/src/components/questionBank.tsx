import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { isGlobalRole } from "../auth/roles";
import { useAction, useApi, useReference } from "../hooks/useApi";
import { API_URL, api, type Page } from "../services/api";
import { MSQ_LETTERS, PATTERN_LABEL, TYPE_LABEL, TYPE_NAME, buildPapers, describeKey, marksText, groupBySubject, numberProblem, reviewPapers,
  type BankQuestion, type BuiltPaper, type MsqContent, type MsqKey, type NatContent, type NatKey, type QuestionType, type QuestionVersion, type Source,
  type SubjectReview } from "../services/questions";
import { Alert, Button, DataTable, EmptyState, Field, Modal, Pagination, SearchField, SelectField, Spinner, StatusBadge, TextAreaField } from "./ui";
import { MathText } from "./MathText";
import { useTargetBranch } from "../hooks/useTargetBranch";
import { requestAlias, suggestPath, type SuggestResult } from "../services/taxonomy";
import type { TaxonomyLevel } from "../services/bulkImports";

type Subject = { subject_id: string; code: string; name: string };
type Facets = { exam_type: string[]; target_class: string[]; chapter: string[]; topic: string[]; subtopic: string[]; difficulty: string[]; pattern: string[] };

const DIFFICULTY_LABEL: Record<string, string> = { EASY: "Easy", MEDIUM: "Medium", HARD: "Hard" };
const SUGGESTED_EXAM_TYPES = ["NEET", "AIIMS", "JEE Main", "JEE Advanced", "CUET", "Board"];

const shorten = (s: string, n = 110) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** The bank's filters and results. `selectable` adds checkboxes (used to build papers, also inside the test wizard). */
export function QuestionBrowser({ source, branchId, providerId, selected, onToggle, selectable, onChanged }: {
  source: Source; branchId?: string; providerId?: string; selected?: Map<string, BankQuestion>; onToggle?: (q: BankQuestion) => void; selectable?: boolean; onChanged?: () => void;
}) {
  const { me } = useAuth();
  const platform = isGlobalRole(me?.primary_role) || !!me?.assignments.some((a) => isGlobalRole(a.role));
  const [f, setF] = useState({ q: "", subject_id: "", exam_type: "", target_class: "", chapter: "", topic: "", subtopic: "", difficulty: "", pattern: "", question_type: "", status: "ACTIVE" });
  const [includeUsed, setIncludeUsed] = useState(true);
  const [origin, setOrigin] = useState("");            // "" | PROVIDER_WIDE | BRANCH | a branch id: where provider questions were created
  const { branches } = useScope();
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<string | null>(null);
  const [editing, setEditing] = useState<BankQuestion | null>(null);
  const [versioning, setVersioning] = useState<BankQuestion | null>(null);
  const act = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF((x) => ({ ...x, [k]: e.target.value })); setPage(1); };
  const base = new URLSearchParams({ scope: source });
  if (branchId && source !== "GLOBAL") base.set("branch_id", branchId);
  else if (providerId && source !== "GLOBAL") base.set("provider_id", providerId);       // "All Branches": the provider's whole bank
  const facetParams = new URLSearchParams(base);
  if (f.subject_id) facetParams.set("subject_id", f.subject_id);
  const params = new URLSearchParams(base);
  Object.entries(f).forEach(([k, v]) => { if (v && k !== "q") params.set(k, v); });
  if (f.q.trim().length >= 2) params.set("q", f.q.trim());
  if (!includeUsed) params.set("include_used", "false");
  if (origin && source !== "GLOBAL") params.set(origin === "PROVIDER_WIDE" || origin === "BRANCH" ? "origin" : "origin_branch_id", origin);
  params.set("page", String(page));
  params.set("page_size", "20");
  const subjects = useReference<Page<Subject>>("/api/v1/subjects?page_size=100");
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
        <SelectField label="Question type" value={f.question_type} onChange={set("question_type")}><option value="">Any</option>
          {(Object.keys(TYPE_NAME) as QuestionType[]).map((t) => <option key={t} value={t}>{TYPE_NAME[t]}</option>)}</SelectField>
        <SelectField label="Pattern" value={f.pattern} onChange={set("pattern")}><option value="">Any</option>{opts(fa?.pattern, (v) => PATTERN_LABEL[v] ?? v)}</SelectField>
        <SelectField label="Status" value={f.status} onChange={set("status")}><option value="ACTIVE">Active</option><option value="ARCHIVED">Archived</option><option value="ALL">All</option></SelectField>
        {source !== "GLOBAL" && (
          <SelectField label="Origin" value={origin} onChange={(e) => { setOrigin(e.target.value); setPage(1); }}>
            <option value="">Any</option><option value="PROVIDER_WIDE">Provider-wide</option><option value="BRANCH">Uploaded by a branch</option>
            {branches.map((b) => <option key={b.branch_id} value={b.branch_id}>Uploaded by {b.name}</option>)}
          </SelectField>
        )}
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
              { header: "Type", render: (q) => <TypeBadge type={q.question_type as QuestionType} /> },
              { header: "Subject", render: (q) => sName(q.subject_id) },
              { header: "Chapter / topic", render: (q) => [q.chapter, q.topic, q.subtopic].filter(Boolean).join(" › ") || "—" },
              { header: "Exam / class", render: (q) => [q.exam_type, q.target_class].filter(Boolean).join(" · ") || "—" },
              { header: "Difficulty", render: (q) => (q.difficulty ? DIFFICULTY_LABEL[q.difficulty] : "—") },
              { header: "Marks", render: (q) => marksText(q.latest) },
              { header: "Source", render: (q) => <StatusBadge status={q.scope === "GLOBAL" ? "GLOBAL" : "PROVIDER"} /> },
              // every provider question is shared with all the provider's branches; the origin only says where it was created
              { header: "Origin", render: (q) => (q.scope === "GLOBAL" ? "Platform" : !q.branch_id ? "Provider-wide"
                : q.origin_branch_name ?? branches.find((b) => b.branch_id === q.branch_id)?.name ?? "A branch") },
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

/** A question or option picture from the authorised endpoint (the session cookie goes with the request; never a storage URL). */
function QuestionPicture({ assetId, alt }: { assetId: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <span className="muted small"> (picture unavailable)</span>;
  return <div className="question-picture"><img src={`${API_URL}/api/v1/question-images/${encodeURIComponent(assetId)}`} alt={alt}
    loading="lazy" onError={() => setFailed(true)} /></div>;
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
          {v.stem && <MathText as="p" className="question-stem" text={v.stem} />}
          {v.stem_image_id && <QuestionPicture assetId={v.stem_image_id} alt="Question picture" />}
          <TypedAnswerView v={v} />
          <p className="small">{v.marks_positive === null ? "Marks: set by each test that uses this question."
            : v.question_type === "MCQ_MULTI" ? `Marks: +${Number(v.marks_positive)} when exactly the correct options are chosen, otherwise 0 (never negative).`
            : `Marks: +${Number(v.marks_positive)} for a correct answer, −${Number(v.marks_negative)} for a wrong one, 0 if unanswered.`}</p>
          {v.explanation && <p className="small muted">Explanation: <MathText text={v.explanation} /></p>}
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

/** Where a question's chapter/topic/subtopic names are looked up: its provider's list, or the platform list (providerId null). */
type Place = { providerId: string | null; subjectId: string; source: "QUESTION" | "CLASSIFY" };

/** The typed value, once the person stops typing for a moment (so suggestions are not fetched on every key). */
function useSettled(value: string, ms = 350) {
  const [settled, setSettled] = useState(value);
  useEffect(() => { const t = window.setTimeout(() => setSettled(value), ms); return () => window.clearTimeout(t); }, [value, ms]);
  return settled;
}

function useSuggest(place: Place | undefined, level: TaxonomyLevel, value: string, parentId: string | null | undefined) {
  const typed = useSettled(value.trim());
  const ready = place?.subjectId && typed && (level === "CHAPTER" || parentId);
  const result = useApi<SuggestResult>(ready ? suggestPath(place.providerId, place.subjectId, level, typed, parentId) : null).data;
  return typed === value.trim() ? result : null;            // never a hint about what was typed before
}

/** "Did you mean …?" under a Chapter / Topic / Subtopic field: the official name is chosen with one click (never automatically), and
 * the typed spelling can be remembered (an alias for administrators; a request to them for everyone else). */
function NameHint({ place, level, value, result, onPick }: { place: Place; level: TaxonomyLevel; value: string; result?: SuggestResult | null;
  onPick: (name: string) => void }) {
  const [picked, setPicked] = useState<{ nodeId: string; typed: string; name: string } | null>(null);
  const act = useAction();
  const [sent, setSent] = useState("");
  const word = level.toLowerCase();
  if (picked && value === picked.name) {
    return (
      <div className="small muted taxonomy-hint" role="status">
        {sent || <>Using the official name. <button type="button" className="link-btn" disabled={act.busy} onClick={() => void act.run(async () => {
          const r = await requestAlias(picked.nodeId, picked.typed, place.source);
          setSent(r.approved ? `“${picked.typed}” will be recognised from now on.` : `Your administrator was asked to accept “${picked.typed}”.`);
        })}>Remember “{picked.typed}” as another spelling</button></>}
        {act.error && <span className="error-text"> {act.error}</span>}
      </div>
    );
  }
  if (!result || !value.trim()) return null;
  if (result.match) {
    return result.match.name !== value.trim() ? <div className="small muted taxonomy-hint">Recognised as “{result.match.name}”.</div> : null;
  }
  const choose = (nodeId: string, name: string) => { setPicked({ nodeId, typed: value.trim(), name }); setSent(""); onPick(name); };
  return (
    <div className="small taxonomy-hint" role="status">
      {result.suggestions.length > 0 ? (
        <>Did you mean{" "}{result.suggestions.map((s, i) => (
          <span key={s.node_id}>{i > 0 && " or "}<button type="button" className="link-btn" onClick={() => choose(s.node_id, s.name)}>
            {s.name}</button>{s.node_id === result.preselect ? " (very likely)" : ""}</span>
        ))}?</>
      ) : result.in_use ? <>“{value.trim()}” is not a {word} of this subject. Choose one from the list.</> : null}
      {result.elsewhere.map((e) => <div key={e.node_id} className="muted">“{e.name}” is a {word} of “{e.parent_name}”.</div>)}
    </div>
  );
}

function ClassificationFields({ value, onChange, facets, place }: { value: ClassFields; onChange: (v: ClassFields) => void; facets?: Facets; place?: Place }) {
  const id = useId();
  const set = (k: keyof ClassFields) => (e: { target: { value: string } }) => onChange({ ...value, [k]: e.target.value });
  const chapter = useSuggest(place, "CHAPTER", value.chapter, null);
  const topic = useSuggest(place, "TOPIC", value.topic, chapter?.match?.node_id);
  const subtopic = useSuggest(place, "SUBTOPIC", value.subtopic, topic?.match?.node_id);
  const hint = (level: TaxonomyLevel, k: "chapter" | "topic" | "subtopic", result?: SuggestResult | null) => place &&
    <NameHint place={place} level={level} value={value[k]} result={result} onPick={(name) => onChange({ ...value, [k]: name })} />;
  const list = (k: string, values: string[]) => <datalist id={`${id}-${k}`}>{values.map((v) => <option key={v} value={v} />)}</datalist>;
  return (
    <>
      <div className="grid cols-2">
        <Field label="Exam type" value={value.exam_type} onChange={set("exam_type")} list={`${id}-exam`} placeholder="NEET" />
        <Field label="Class / target level" value={value.target_class} onChange={set("target_class")} list={`${id}-class`} placeholder="Class 12" />
        <div><Field label="Chapter" value={value.chapter} onChange={set("chapter")} list={`${id}-chapter`} />{hint("CHAPTER", "chapter", chapter)}</div>
        <div><Field label="Topic" value={value.topic} onChange={set("topic")} list={`${id}-topic`} />{hint("TOPIC", "topic", topic)}</div>
        <div><Field label="Subtopic" value={value.subtopic} onChange={set("subtopic")} list={`${id}-subtopic`} />{hint("SUBTOPIC", "subtopic", subtopic)}</div>
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
      <ClassificationFields value={v} onChange={setV} facets={facets}
        place={{ providerId: question.provider_id, subjectId: question.subject_id, source: "CLASSIFY" }} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} onClick={() => void run(async () => { await api(`/api/v1/questions/${question.question_id}`, { method: "PATCH", body: nullIfEmpty(v) }); onDone(); })}>Save</Button>
      </div>
    </Modal>
  );
}

/** The question's type as a short badge (MCQ / MSQ / NAT), named in full for screen readers. */
export function TypeBadge({ type }: { type?: QuestionType }) {
  const t = type ?? "MCQ_SINGLE";
  return <span className={`badge type-${TYPE_LABEL[t].toLowerCase()}`} title={TYPE_NAME[t]}>{TYPE_LABEL[t]}<span className="sr-only"> ({TYPE_NAME[t]})</span></span>;
}

/** Options (MCQ, MSQ) with the correct ones marked, or a NAT's accepted answer; nothing of the key when it is hidden. */
export function TypedAnswerView({ v }: { v: QuestionVersion }) {
  const type = v.question_type ?? "MCQ_SINGLE";
  if (type === "NUMERICAL") {
    const c = v.content as NatContent | null;
    return (
      <p className="small">Answer type: {c?.format === "INTEGER" ? "a whole number" : `a number (up to ${c?.decimals ?? 6} decimal places)`}{c?.unit ? `, in ${c.unit}` : ""}.
        {v.answer_key && <> Accepted answer: <strong>{describeKey(type, v)}</strong>{c?.unit ? ` ${c.unit}` : ""}.</>}</p>
    );
  }
  const options = type === "MCQ_MULTI" ? ((v.content as MsqContent | null)?.options ?? []).map((o) => ({ id: o.id, text: o.text, image: o.image ?? null }))
    : [v.option_a, v.option_b, v.option_c, v.option_d].map((text, i) => ({ id: "ABCD"[i], text,
      image: [v.option_a_image_id, v.option_b_image_id, v.option_c_image_id, v.option_d_image_id][i] ?? null }));
  const correct = new Set(type === "MCQ_MULTI" ? (v.answer_key as MsqKey | null)?.correct ?? [] : v.correct_option ? [v.correct_option] : []);
  return (
    <>
      {type === "MCQ_MULTI" && <p className="small muted">One or more options are correct; a student must choose exactly those.</p>}
      <ol type="A" className="options">
        {options.map((o) => (
          <li key={o.id} className={correct.has(o.id) ? "correct" : undefined}>
            {o.text && <MathText text={o.text} />}{o.image && <QuestionPicture assetId={o.image} alt={`Option ${o.id} picture`} />}
            {correct.has(o.id) && <span className="sr-only"> (correct answer)</span>}
          </li>
        ))}
      </ol>
    </>
  );
}

type ContentFields = {
  question_type: QuestionType; stem: string; option_a: string; option_b: string; option_c: string; option_d: string; correct_option: string;
  marks_positive: string; marks_negative: string; explanation: string;
  msq: { text: string; image: string | null }[]; msq_correct: string[];                       // MSQ: options A.. and the correct ones
  nat_answer: string; nat_kind: "EXACT" | "ABS" | "RANGE"; nat_amount: string; nat_min: string; nat_max: string;   // NAT
  nat_format: "INTEGER" | "DECIMAL"; nat_decimals: string; nat_unit: string;
};
const EMPTY_CONTENT: ContentFields = { question_type: "MCQ_SINGLE", stem: "", option_a: "", option_b: "", option_c: "", option_d: "", correct_option: "A",
  marks_positive: "", marks_negative: "", explanation: "", msq: [0, 1, 2, 3].map(() => ({ text: "", image: null })), msq_correct: [],
  nat_answer: "", nat_kind: "EXACT", nat_amount: "", nat_min: "", nat_max: "", nat_format: "DECIMAL", nat_decimals: "2", nat_unit: "" };

function fromVersion(v: QuestionVersion): ContentFields {
  const marks = (x: string | null) => (x === null ? "" : String(Number(x)));
  const c: ContentFields = { ...EMPTY_CONTENT, question_type: v.question_type ?? "MCQ_SINGLE", stem: v.stem, option_a: v.option_a, option_b: v.option_b,
    option_c: v.option_c, option_d: v.option_d, correct_option: v.correct_option ?? "A", marks_positive: marks(v.marks_positive),
    marks_negative: marks(v.marks_negative), explanation: v.explanation ?? "" };
  if (c.question_type === "MCQ_MULTI") {
    c.msq = ((v.content as MsqContent | null)?.options ?? []).map((o) => ({ text: o.text ?? "", image: o.image ?? null }));
    c.msq_correct = (v.answer_key as MsqKey | null)?.correct ?? [];
  } else if (c.question_type === "NUMERICAL") {
    const n = v.content as NatContent | null, k = v.answer_key as NatKey | null;
    Object.assign(c, { nat_format: n?.format ?? "DECIMAL", nat_decimals: String(n?.decimals ?? 2), nat_unit: n?.unit ?? "",
      nat_answer: k?.value ?? "", nat_kind: k?.tolerance.kind ?? "EXACT", nat_amount: k?.tolerance.amount ?? "", nat_min: k?.tolerance.min ?? "",
      nat_max: k?.tolerance.max ?? "" });
  }
  return c;
}

/** Why a NAT's answer fields cannot be saved yet ({field: message}); empty when they can. Mirrors the server's rulebook. */
function natProblems(c: ContentFields): Record<string, string> {
  const out: Record<string, string> = {};
  const integer = c.nat_format === "INTEGER";
  const decimals = integer ? 0 : Number(c.nat_decimals || "6");
  if (!integer && !(/^[0-6]$/.test(c.nat_decimals.trim()) || !c.nat_decimals.trim())) out.nat_decimals = "From 0 to 6.";
  const check = (k: keyof ContentFields, text: string, places = decimals, asInteger = integer) => {
    const p = text.trim() ? numberProblem(text, places, asInteger) : "Required.";
    if (p) out[k] = p;
  };
  check("nat_answer", c.nat_answer);
  if (c.nat_kind === "ABS") {
    check("nat_amount", c.nat_amount, 6, false);
    if (!out.nat_amount && c.nat_amount.trim().startsWith("-")) out.nat_amount = "The tolerance cannot be negative.";
  }
  if (c.nat_kind === "RANGE") {
    check("nat_min", c.nat_min);
    check("nat_max", c.nat_max);
    if (!out.nat_min && !out.nat_max && !out.nat_answer) {
      const [lo, hi, v] = [Number(c.nat_min), Number(c.nat_max), Number(c.nat_answer)];
      if (lo > hi) out.nat_min = "The minimum must not be more than the maximum.";
      else if (v < lo || v > hi) out.nat_answer = "The answer must lie between the minimum and the maximum.";
    }
  }
  if (c.nat_unit.length > 30) out.nat_unit = "At most 30 characters.";
  return out;
}

function ContentFieldsForm({ value, onChange, typeLocked }: { value: ContentFields; onChange: (v: ContentFields) => void; typeLocked?: boolean }) {
  const set = (k: keyof ContentFields) => (e: { target: { value: string } }) => onChange({ ...value, [k]: e.target.value });
  const type = value.question_type;
  const nat = type === "NUMERICAL" ? natProblems(value) : {};
  const shown = (k: string, text: string) => (text.trim() ? nat[k] : undefined);       // no error on a field not typed in yet
  const setOption = (i: number, text: string) => onChange({ ...value, msq: value.msq.map((o, j) => (j === i ? { ...o, text } : o)) });
  const toggle = (id: string) => onChange({ ...value, msq_correct: value.msq_correct.includes(id) ? value.msq_correct.filter((x) => x !== id) : [...value.msq_correct, id].sort() });
  const removeLast = () => {
    const gone = MSQ_LETTERS[value.msq.length - 1];
    onChange({ ...value, msq: value.msq.slice(0, -1), msq_correct: value.msq_correct.filter((x) => x !== gone) });
  };
  return (
    <>
      {typeLocked ? <p className="small">Question type: <TypeBadge type={type} /> {TYPE_NAME[type]} <span className="muted">(a question keeps its type)</span></p> : (
        <fieldset className="field">
          <legend>Question type</legend>
          <div className="row">
            {(Object.keys(TYPE_NAME) as QuestionType[]).map((t) => (
              <label key={t} className="row checkbox-field"><input type="radio" name="question-type" value={t} checked={type === t}
                onChange={() => onChange({ ...value, question_type: t })} /> {TYPE_NAME[t]}</label>
            ))}
          </div>
        </fieldset>
      )}
      <TextAreaField label="Question" value={value.stem} onChange={set("stem")} required />
      {type === "MCQ_SINGLE" && (
        <div className="grid cols-2">
          {(["option_a", "option_b", "option_c", "option_d"] as const).map((k) => <Field key={k} label={`Option ${k.slice(-1).toUpperCase()}`} value={value[k]} onChange={set(k)} required />)}
        </div>
      )}
      {type === "MCQ_MULTI" && (
        <fieldset className="field">
          <legend>Options (2 to 8) - tick every correct option</legend>
          <div className="stack">
            {value.msq.map((o, i) => {
              const id = MSQ_LETTERS[i];
              return (
                <div key={id} className="row">
                  <label className="row checkbox-field"><input type="checkbox" checked={value.msq_correct.includes(id)} onChange={() => toggle(id)}
                    aria-label={`Option ${id} is correct`} /> Correct</label>
                  {o.image ? <span className="small">Option {id}: <QuestionPicture assetId={o.image} alt={`Option ${id} picture`} /></span>
                    : <Field label={`Option ${id}`} value={o.text} onChange={(e) => setOption(i, e.target.value)} required />}
                </div>
              );
            })}
            <div className="row">
              <Button small variant="secondary" disabled={value.msq.length >= 8} onClick={() => onChange({ ...value, msq: [...value.msq, { text: "", image: null }] })}>Add option</Button>
              <Button small variant="secondary" disabled={value.msq.length <= 2} onClick={removeLast}>Remove option {MSQ_LETTERS[value.msq.length - 1]}</Button>
            </div>
          </div>
        </fieldset>
      )}
      {type === "NUMERICAL" && (
        <>
          <div className="grid cols-3">
            <SelectField label="Answer format" value={value.nat_format} onChange={set("nat_format")}><option value="INTEGER">Whole number</option><option value="DECIMAL">Decimal</option></SelectField>
            {value.nat_format === "DECIMAL" && <Field label="Decimal places (0 to 6)" value={value.nat_decimals} onChange={set("nat_decimals")} inputMode="numeric" error={nat.nat_decimals} />}
            <Field label="Unit (optional, shown to students)" value={value.nat_unit} onChange={set("nat_unit")} placeholder="m/s²" error={nat.nat_unit} />
          </div>
          <div className="grid cols-3">
            <Field label="Correct answer" value={value.nat_answer} onChange={set("nat_answer")} inputMode="decimal" required error={shown("nat_answer", value.nat_answer)}
              hint="Digits only: no commas, units or exponents" />
            <SelectField label="Accept" value={value.nat_kind} onChange={set("nat_kind")}>
              <option value="EXACT">Only this number</option><option value="ABS">This number ± a tolerance</option><option value="RANGE">Any number in a range</option>
            </SelectField>
            {value.nat_kind === "ABS" && <Field label="Tolerance (±)" value={value.nat_amount} onChange={set("nat_amount")} inputMode="decimal" required error={shown("nat_amount", value.nat_amount)} hint="The limits count as correct" />}
          </div>
          {value.nat_kind === "RANGE" && (
            <div className="grid cols-2">
              <Field label="Minimum (accepted)" value={value.nat_min} onChange={set("nat_min")} inputMode="decimal" required error={shown("nat_min", value.nat_min)} />
              <Field label="Maximum (accepted)" value={value.nat_max} onChange={set("nat_max")} inputMode="decimal" required error={shown("nat_max", value.nat_max)} />
            </div>
          )}
        </>
      )}
      <div className="grid cols-3">
        {type === "MCQ_SINGLE" && <SelectField label="Correct answer" value={value.correct_option} onChange={set("correct_option")}>{["A", "B", "C", "D"].map((o) => <option key={o}>{o}</option>)}</SelectField>}
        <Field label="Marks (correct, optional)" value={value.marks_positive} onChange={set("marks_positive")} inputMode="decimal"
          hint="Leave empty: each test sets the marks" />
        {type === "MCQ_MULTI" ? <Field label="Negative marks" value="0" disabled hint="MSQ is all-or-nothing: a wrong choice scores 0, never negative" />
          : <Field label="Negative marks (wrong, optional)" value={value.marks_negative} onChange={set("marks_negative")} inputMode="decimal"
            hint="Deducted; empty or 0 for none" />}
      </div>
      <TextAreaField label="Explanation (optional, shown at result review)" value={value.explanation} onChange={set("explanation")} />
    </>
  );
}

// A question's own marks are optional (marks belong to the test). When given: up to 2 decimal places, correct > 0; the server checks
// the same rules again.
const MARK = /^\d{1,4}(\.\d{1,2})?$/;
const marksReady = (c: ContentFields) => (!c.marks_positive.trim() && (c.question_type === "MCQ_MULTI" || !c.marks_negative.trim()))
  || (MARK.test(c.marks_positive.trim()) && Number(c.marks_positive) > 0
    && (c.question_type === "MCQ_MULTI" || !c.marks_negative.trim() || MARK.test(c.marks_negative.trim())));
const contentReady = (c: ContentFields) => (c.stem.trim().length >= 3 || (c.question_type !== "MCQ_SINGLE" && !!c.stem.trim())) && marksReady(c) && (
  c.question_type === "MCQ_SINGLE" ? [c.option_a, c.option_b, c.option_c, c.option_d].every((o) => o.trim())
    : c.question_type === "MCQ_MULTI" ? c.msq.length >= 2 && c.msq.every((o) => o.text.trim() || o.image) && c.msq_correct.length > 0
      : Object.keys(natProblems(c)).length === 0);
const contentBody = (c: ContentFields) => {
  const common = { stem: c.stem, explanation: c.explanation.trim() || null, marks_positive: c.marks_positive.trim() || null };
  if (c.question_type === "MCQ_SINGLE") {
    return { ...common, option_a: c.option_a, option_b: c.option_b, option_c: c.option_c, option_d: c.option_d, correct_option: c.correct_option,
      marks_negative: c.marks_negative.trim() || null };
  }
  if (c.question_type === "MCQ_MULTI") {
    return { ...common, question_type: c.question_type, marks_negative: common.marks_positive ? "0" : null,
      content: { options: c.msq.map((o, i) => ({ id: MSQ_LETTERS[i], text: o.image ? null : o.text.trim(), image: o.image })) },
      answer_key: { correct: c.msq_correct } };
  }
  const tolerance = c.nat_kind === "ABS" ? { kind: "ABS", amount: c.nat_amount.trim() }
    : c.nat_kind === "RANGE" ? { kind: "RANGE", min: c.nat_min.trim(), max: c.nat_max.trim() } : { kind: "EXACT" };
  return { ...common, question_type: c.question_type, marks_negative: c.marks_negative.trim() || (common.marks_positive ? "0" : null),
    content: { format: c.nat_format, decimals: c.nat_format === "INTEGER" ? 0 : Number(c.nat_decimals || "6"), unit: c.nat_unit.trim() || null },
    answer_key: { value: c.nat_answer.trim(), tolerance } };
};

function NewQuestionVersion({ question, onClose, onDone }: { question: BankQuestion; onClose: () => void; onDone: () => void }) {
  const v = question.latest;
  const [c, setC] = useState<ContentFields>(() => fromVersion({ ...v, question_type: (v.question_type ?? question.question_type) as QuestionType }));
  const { busy, error, run } = useAction();
  return (
    <Modal title={`New version of the question (now v${v.version_no})`} onClose={onClose}>
      <Alert kind="info">Saving creates version {v.version_no + 1}. Papers and scheduled exams keep the version they already use{question.usage_count ? ` (${question.usage_count} paper version(s))` : ""}.</Alert>
      {error && <Alert kind="error">{error}</Alert>}
      <ContentFieldsForm value={c} onChange={setC} typeLocked />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!contentReady(c)} onClick={() => void run(async () => { await api(`/api/v1/questions/${question.question_id}/versions`, { method: "POST", body: contentBody(c) }); onDone(); })}>Save as new version</Button>
      </div>
    </Modal>
  );
}

/** Add a question to the platform bank (GLOBAL, platform roles) or to a provider branch's bank. */
export function AddQuestion({ scope, branchId, providerId, onClose, onDone }: { scope: "GLOBAL" | "PROVIDER"; branchId?: string; providerId?: string; onClose: () => void; onDone: () => void }) {
  const { me } = useAuth();
  // provider-wide questions are the Provider Admin's (the server decides; this only decides whether to offer the choice)
  const mayAddProviderWide = scope === "PROVIDER" && !!providerId && !!me?.assignments.some((a) => a.role === "PROVIDER_ADMIN" && a.provider_id === providerId);
  const [providerWide, setProviderWide] = useState(false);
  const { target, field: branchField } = useTargetBranch(scope === "PROVIDER" ? branchId : "global");   // "All Branches": choose one
  const catalogue = useReference<Page<Subject>>(scope === "GLOBAL" ? "/api/v1/subjects?page_size=100" : null);
  const offered = useReference<Subject[]>(scope === "PROVIDER" && providerId ? `/api/v1/providers/${providerId}/subjects` : null);
  const subjects = scope === "GLOBAL" ? catalogue.data?.items ?? [] : offered.data ?? [];
  const [subjectId, setSubjectId] = useState("");
  const facets = useApi<Facets>(`/api/v1/question-taxonomy?scope=${scope}${branchId && scope === "PROVIDER" ? `&branch_id=${branchId}` : ""}${subjectId ? `&subject_id=${subjectId}` : ""}`);
  const [k, setK] = useState<ClassFields>({ exam_type: "", target_class: "", chapter: "", topic: "", subtopic: "", difficulty: "", pattern: "SINGLE_CORRECT", subtype: "" });
  const [c, setC] = useState<ContentFields>(EMPTY_CONTENT);
  const { busy, error, run } = useAction();
  return (
    <Modal title={scope === "GLOBAL" ? "Add a platform question" : "Add a question"} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      {mayAddProviderWide && (
        <label className="row checkbox-field"><input type="checkbox" checked={providerWide} onChange={(e) => setProviderWide(e.target.checked)} />
          Provider-wide (not tied to this branch)</label>
      )}
      <p className="muted small">{scope === "GLOBAL" ? "Platform questions are available to every institute."
        : "The question is shared with every branch of the institute; only its own branch and the provider administrators can change it."}</p>
      {!(providerWide && mayAddProviderWide) && branchField}
      <SelectField label="Subject" value={subjectId} onChange={(e) => setSubjectId(e.target.value)} required>
        <option value="">Select…</option>{subjects.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
      </SelectField>
      <ClassificationFields value={k} onChange={setK} facets={facets.data ?? undefined}
        place={subjectId && (scope === "GLOBAL" || providerId) ? { providerId: scope === "GLOBAL" ? null : providerId!, subjectId, source: "QUESTION" } : undefined} />
      <ContentFieldsForm value={c} onChange={setC} />
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!subjectId || !contentReady(c)} onClick={() => void run(async () => {
          const where = scope === "GLOBAL" ? {} : providerWide && mayAddProviderWide ? { provider_id: providerId } : { branch_id: target };
          await api("/api/v1/questions", { method: "POST", body: { scope, ...where, subject_id: subjectId, ...nullIfEmpty(k), pattern: k.pattern, ...contentBody(c) } });
          onDone();
        })}>Add question</Button>
      </div>
    </Modal>
  );
}

/** The possible duplicates of the papers about to be built, named by their question text; never blocking. */
export function ReviewFlags({ reviews, stemOf, subjectName }: { reviews: SubjectReview[]; stemOf: (questionId: string) => string; subjectName: (id: string) => string }) {
  const blocking = reviews.flatMap((r) => r.blocking.map((f) => ({ r, f })));
  const flags = reviews.flatMap((r) => r.flags.map((f) => ({ r, f })));
  const list = (items: typeof flags) => (
    <ul className="small duplicate-list">
      {items.map(({ r, f }) => (
        <li key={`${r.subject_id}-${f.positions.join("-")}`}><strong>{subjectName(r.subject_id)}</strong>: {f.reason}
          <ul>{f.positions.map((pos) => <li key={pos}>Question {pos} of your selection: “{shorten(stemOf(r.question_ids[pos - 1]), 90)}”</li>)}</ul></li>
      ))}
    </ul>
  );
  return (
    <>
      {blocking.length > 0 && (
        <Alert kind="error"><p>The same question is selected twice. Unselect one question of each pair below, then create the paper.</p>{list(blocking)}</Alert>
      )}
      {flags.length > 0 && (
        <Alert kind="warn"><p>These questions may be the same question. Check them, then create anyway or change the selection.</p>{list(flags)}</Alert>
      )}
    </>
  );
}

export function BuildPapersDialog({ scope, branchId, selected, onClose, onDone }: { scope: "GLOBAL" | "PROVIDER"; branchId?: string; selected: Map<string, BankQuestion>;
  onClose: () => void; onDone: (papers: BuiltPaper[]) => void }) {
  const subjects = useReference<Page<Subject>>("/api/v1/subjects?page_size=100");
  const groups = groupBySubject(selected);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [meta, setMeta] = useState({ exam_type: "", target_class: "" });
  const [reviews, setReviews] = useState<SubjectReview[] | null>(null);    // shown once; "Create anyway" then builds
  const { busy, error, run } = useAction();
  const { can } = useAuth();
  const sName = (id: string) => subjects.data?.items.find((s) => s.subject_id === id)?.name ?? "Subject";
  const { target, field: branchField } = useTargetBranch(scope === "PROVIDER" ? branchId : "global");   // a paper belongs to one branch
  const ready = groups.every((g) => (titles[g.subject_id] ?? "").trim().length >= 3) && (scope === "GLOBAL" || !!target);
  return (
    <Modal title="Create paper from selected questions" onClose={onClose}>
      <p className="muted small">{selected.size} question(s) in {groups.length} subject(s). {groups.length > 1 ? "One paper is created per subject; combine them in a test." : ""}
        {" "}The new version starts as a draft (VALIDATED){can("paper.approve") ? ", ready for you to approve and publish" : " for approval"}.</p>
      {error && <Alert kind="error">{error}</Alert>}
      {reviews && <ReviewFlags reviews={reviews} stemOf={(id) => selected.get(id)?.latest.stem ?? ""} subjectName={sName} />}
      {branchField}
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
        <Button busy={busy} disabled={!ready || groups.length === 0 || !!reviews?.some((r) => r.blocking.length)} onClick={() => void run(async () => {
          const named = groups.map((g) => ({ ...g, title: titles[g.subject_id].trim() }));
          if (!reviews) {
            const found = await reviewPapers(scope, target, named, meta);
            if (found.length) { setReviews(found); return; }
          }
          onDone(await buildPapers(scope, target, named, meta));
        })}>{reviews && !reviews.some((r) => r.blocking.length) ? "Create anyway" : `Create ${groups.length > 1 ? `${groups.length} papers` : "paper"}`}</Button>
      </div>
    </Modal>
  );
}
