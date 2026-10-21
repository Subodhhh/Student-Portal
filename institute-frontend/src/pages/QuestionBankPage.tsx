import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { isGlobalRole } from "../auth/roles";
import NeedBranch from "../components/NeedBranch";
import { AddQuestion, BuildPapersDialog, QuestionBrowser } from "../components/questionBank";
import type { BankQuestion, BuiltPaper, Source } from "../services/questions";
import { Alert, Button, Card, DataTable, EmptyState, Field, PageHeader, Pagination, SearchField, SelectField, Spinner, StatusBadge, Tabs } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api, type Page } from "../services/api";

type Subject = { subject_id: string; code: string; name: string };
type PV = { paper_version_id: string; version_no: number; status: string; question_count: number; total_marks: string };
type PaperRow = { paper_id: string; scope: string; subject_id: string; title: string; exam_type: string | null; target_class: string | null; latest_version: PV | null };
type UploadError = { row: number | null; field: string | null; message: string };
type UploadRow = { upload_id: string; title: string | null; subject_id: string; status: string; question_count: number | null; errors: UploadError[]; error_count: number;
  has_answer_key: boolean; paper_version_id: string | null; paper_version_status: string | null; version_no: number | null; created_at: string };

/** Question Bank & Papers: reusable questions, versioned papers built from them, and the history of file imports. */
export default function QuestionBankPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "bank";
  return (
    <>
      <Tabs active={tab} onChange={(id) => setParams({ tab: id }, { replace: true })}
        tabs={[{ id: "bank", label: "Question Bank" }, { id: "papers", label: "Papers" }, { id: "imports", label: "Import Processing" }]} />
      {tab === "bank" && <BankTab />}
      {tab === "papers" && <PapersTab />}
      {tab === "imports" && <NeedBranch>{(ids) => <ImportTab {...ids} />}</NeedBranch>}
    </>
  );
}

// ------------------------------------------------------------------ question bank

function SourcePicker({ value, onChange, platformOnly }: { value: Source; onChange: (s: Source) => void; platformOnly?: boolean }) {
  return (
    <SelectField label="Questions from" value={value} onChange={(e) => onChange(e.target.value as Source)}>
      <option value="GLOBAL">Platform question bank</option>
      {!platformOnly && <option value="PROVIDER">A provider branch</option>}
      {!platformOnly && <option value="ALL">A provider branch + platform bank</option>}
    </SelectField>
  );
}

function BankTab() {
  const { me, can } = useAuth();
  const global = isGlobalRole(me?.primary_role);
  const [source, setSource] = useState<Source>(global ? "GLOBAL" : "ALL");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<Map<string, BankQuestion>>(new Map());
  const [building, setBuilding] = useState(false);
  const [built, setBuilt] = useState<BuiltPaper[] | null>(null);
  const [version, setVersion] = useState(0);
  const toggle = (q: BankQuestion) => setSelected((cur) => {
    const next = new Map(cur);
    if (next.has(q.question_id)) next.delete(q.question_id); else next.set(q.question_id, q);
    return next;
  });
  const reset = (s: Source) => { setSource(s); setSelected(new Map()); setBuilt(null); };
  const paperScope = source === "GLOBAL" ? "GLOBAL" : "PROVIDER";
  // A provider paper may combine the branch's questions with platform questions; a platform paper only platform questions.
  const canBuild = paperScope === "GLOBAL" ? global && can("paper.create") : can("paper.create");
  const intro = (
    <PageHeader subtitle="Reusable questions classified by exam type, class, subject, chapter, topic and subtopic. Papers reference questions; nothing is copied." />
  );
  const body = (branchId?: string, providerId?: string) => (
    <div className="stack">
      <div className="toolbar">
        <SourcePicker value={source} onChange={reset} />
        {((source === "GLOBAL" && global && can("question.manage")) || (source !== "GLOBAL" && can("question.manage"))) && (
          <Button onClick={() => setAdding(true)}>{source === "GLOBAL" ? "Add platform question" : "Add question"}</Button>
        )}
      </div>
      {built && (
        <Alert kind="success">
          Created {built.map((p, i) => <span key={p.paper_version_id}>{i > 0 && ", "}<Link to={`/papers/${p.paper_version_id}`}>paper version {p.version_no} ({p.question_count} questions)</Link></span>)}.
          Review it, then approve and publish it from the paper page.
        </Alert>
      )}
      {selected.size > 0 && (
        <div className="selection-bar" role="region" aria-label="Selected questions">
          <span><strong>{selected.size}</strong> question(s) selected</span>
          {canBuild && <Button small onClick={() => setBuilding(true)}>Create paper from selection</Button>}
          <Button small variant="secondary" onClick={() => setSelected(new Map())}>Clear selection</Button>
        </div>
      )}
      <QuestionBrowser key={`${source}-${branchId ?? ""}-${version}`} source={source} branchId={branchId} selectable={canBuild} selected={selected} onToggle={toggle} />
      {adding && <AddQuestion scope={source === "GLOBAL" ? "GLOBAL" : "PROVIDER"} branchId={branchId} providerId={providerId}
        onClose={() => setAdding(false)} onDone={() => { setAdding(false); setVersion((v) => v + 1); }} />}
      {building && <BuildPapersDialog scope={paperScope} branchId={branchId} selected={selected} onClose={() => setBuilding(false)}
        onDone={(papers) => { setBuilding(false); setBuilt(papers); setSelected(new Map()); setVersion((v) => v + 1); }} />}
    </div>
  );
  return (
    <>
      {intro}
      {source === "GLOBAL" ? body() : <NeedBranch>{({ providerId, branchId }) => body(branchId, providerId)}</NeedBranch>}
    </>
  );
}

// ------------------------------------------------------------------ papers

function PapersTab() {
  const { me } = useAuth();
  const global = isGlobalRole(me?.primary_role);
  const [source, setSource] = useState<"GLOBAL" | "PROVIDER">(global ? "GLOBAL" : "PROVIDER");
  return (
    <>
      <PageHeader subtitle="Versioned papers. Published versions never change: a correction becomes the next version, and exams keep the version they used." />
      <div className="toolbar">
        <SelectField label="Papers from" value={source} onChange={(e) => setSource(e.target.value as "GLOBAL" | "PROVIDER")}>
          <option value="GLOBAL">Platform papers</option><option value="PROVIDER">A provider branch</option>
        </SelectField>
      </div>
      {source === "GLOBAL" ? <PaperList scope="GLOBAL" /> : <NeedBranch>{({ branchId }) => <PaperList scope="PROVIDER" branchId={branchId} />}</NeedBranch>}
    </>
  );
}

function PaperList({ scope, branchId }: { scope: "GLOBAL" | "PROVIDER"; branchId?: string }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const subjects = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  const params = new URLSearchParams({ scope, page: String(page), page_size: "20" });
  if (branchId) params.set("branch_id", branchId);
  if (q.trim()) params.set("q", q.trim());
  if (status) params.set("status", status);
  if (subjectId) params.set("subject_id", subjectId);
  const list = useApi<Page<PaperRow>>(`/api/v1/papers?${params.toString()}`);
  const sName = (id: string) => subjects.data?.items.find((s) => s.subject_id === id)?.name ?? "—";
  return (
    <div className="stack">
      <div className="toolbar">
        <SearchField label="Search by title" value={q} onChange={(v) => { setQ(v); setPage(1); }} />
        <SelectField label="Subject" value={subjectId} onChange={(e) => { setSubjectId(e.target.value); setPage(1); }}>
          <option value="">All</option>{(subjects.data?.items ?? []).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
        </SelectField>
        <SelectField label="Has a version that is" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">Any status</option>{["VALIDATED", "APPROVED", "PUBLISHED", "SUPERSEDED", "WITHDRAWN"].map((s) => <option key={s} value={s}>{s.toLowerCase()}</option>)}
        </SelectField>
      </div>
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(p) => p.paper_id}
            empty={<EmptyState title="No papers" hint="Build a paper from the Question Bank tab, or import a file under Import Processing." />}
            columns={[
              { header: "Paper", render: (p) => (p.latest_version ? <Link to={`/papers/${p.latest_version.paper_version_id}`}>{p.title}</Link> : p.title) },
              { header: "Subject", render: (p) => sName(p.subject_id) },
              { header: "Exam / class", render: (p) => [p.exam_type, p.target_class].filter(Boolean).join(" · ") || "—" },
              { header: "Latest version", render: (p) => (p.latest_version ? `v${p.latest_version.version_no} · ${p.latest_version.question_count} q · ${Number(p.latest_version.total_marks)} marks` : "—") },
              { header: "Status", render: (p) => (p.latest_version ? <StatusBadge status={p.latest_version.status} /> : "—") },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ import processing

const TEMPLATE = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks,explanation,topic\n\"What is the SI unit of force?\",Newton,Joule,Watt,Pascal,A,4,1,\"F = ma\",Mechanics\n";
const KEY_TEMPLATE = "question,answer\n1,A\n2,C\n3,B\n";
const ACCEPT = ".csv,.xlsx,.docx,.pdf,.png,.jpg,.jpeg,text/csv,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf";

function ImportTab({ providerId, branchId }: { providerId: string; branchId: string }) {
  const { can } = useAuth();
  const scope = useScope();
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const history = useApi<Page<UploadRow>>(`/api/v1/uploads?branch_id=${branchId}&page=${page}&page_size=20${status ? `&status=${status}` : ""}`);
  const [subjectId, setSubjectId] = useState("");
  const [title, setTitle] = useState("");
  const [useKey, setUseKey] = useState(false);
  const [errors, setErrors] = useState<UploadError[]>([]);
  const [ok, setOk] = useState<{ version: string; count: number } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const key = useRef<HTMLInputElement>(null);
  const up = useAction();
  const sName = (id: string) => subjects.data?.find((s) => s.subject_id === id)?.name ?? "—";
  const upload = () => up.run(async () => {
    setErrors([]); setOk(null);
    const f = file.current?.files?.[0];
    if (!f) throw new Error("Choose the question file first.");
    const form = new FormData();
    form.append("file", f);
    form.append("title", title);
    if (useKey) {
      const k = key.current?.files?.[0];
      if (!k) throw new Error("Choose the answer-key file, or untick the separate answer key.");
      form.append("answer_key", k);
    }
    try {
      const r = await api<{ question_count: number; paper_version_id: string }>(`/api/v1/branches/${branchId}/subjects/${subjectId}/papers/upload`, { method: "POST", form });
      setOk({ version: r.paper_version_id, count: r.question_count });
      setTitle("");
      if (file.current) file.current.value = "";
      if (key.current) key.current.value = "";
    } catch (e) {
      if (e instanceof ApiError && e.code === "EXTRACTION_FAILED") {
        setErrors((e.extra.errors as UploadError[]) ?? []);
        throw new Error("The paper was rejected. No question was imported: fix every problem below and upload again.", { cause: e });
      }
      throw e;
    } finally {
      history.reload();
    }
  });
  return (
    <div className="stack">
      <PageHeader subtitle={`Imports into ${scope.branch?.name ?? "this branch"}. Files are validated completely when uploaded: one problem rejects the whole paper.`} />
      {can("paper.upload") && (
        <Card>
          <div className="card-head"><h2>Photos of a printed paper</h2><Link className="btn" to="/papers/photo">Upload photos</Link></div>
          <p className="muted small">Take photos of each page (and, separately, of the answer key) with your phone. The questions are read automatically and shown for you to
            check and correct before anything is imported.</p>
        </Card>
      )}
      {can("paper.upload") && (
        <Card>
          <h2>Upload a question file</h2>
          <p className="muted small">
            CSV or one-sheet Excel (.xlsx) with the template columns, Word (.docx) or a text PDF with numbered questions. Scanned PNG/JPEG pages are read only where the
            server has image extraction enabled, and PDF reading needs the server's PDF library; otherwise those files are rejected with a clear message.{" "}
            <a href={`data:text/csv;charset=utf-8,${encodeURIComponent(TEMPLATE)}`} download="paper-template.csv">Download the question template</a>
          </p>
          <details className="small">
            <summary>File formats and the separate answer key</summary>
            <p>Columns (any order): <code>question</code>, <code>option_a</code>–<code>option_d</code>, <code>correct</code> (A–D), <code>marks</code>, <code>negative_marks</code>;
              optional <code>explanation</code>, <code>topic</code>. Word/PDF: a table with those columns, or numbered questions with “A) … D)” options and an “Answer:” line.</p>
            <p><strong>Separate answer key:</strong> leave the answers out of the question file (no <code>correct</code> column or “Answer:” lines) and upload a .csv/.txt key with
              one line per question: <code>number,answer</code> (for example <code>1,A</code>). Question numbers follow the order in the file. If the paper also contains answers,
              they must match the key. A missing, extra, duplicated or mismatched answer rejects the whole paper.{" "}
              <a href={`data:text/csv;charset=utf-8,${encodeURIComponent(KEY_TEMPLATE)}`} download="answer-key-template.csv">Download the key template</a></p>
          </details>
          {up.error && <Alert kind="error">{up.error}</Alert>}
          {ok && <Alert kind="success">{ok.count} questions validated and imported as a draft. <Link to={`/papers/${ok.version}`}>Review it</Link>, then approve and publish.</Alert>}
          {errors.length > 0 && (
            <DataTable rows={errors} rowKey={(e) => `${e.row}-${e.field}-${e.message}`}
              columns={[{ header: "Row / question", render: (e) => e.row ?? "—" }, { header: "Field", render: (e) => e.field ?? "—" }, { header: "Problem", render: (e) => e.message }]} />
          )}
          <div className="grid cols-2">
            <SelectField label="Subject" value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
              <option value="">Select…</option>{(subjects.data ?? []).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
            </SelectField>
            <Field label="Paper title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="field"><label htmlFor="paper-file">Question file (max 2 MB)</label><input id="paper-file" type="file" accept={ACCEPT} ref={file} /></div>
          <label className="row checkbox-field"><input type="checkbox" checked={useKey} onChange={(e) => setUseKey(e.target.checked)} /> The answers are in a separate answer-key file</label>
          {useKey && <div className="field"><label htmlFor="key-file">Answer key (.csv or .txt)</label><input id="key-file" type="file" accept=".csv,.txt,text/csv,text/plain" ref={key} /></div>}
          <div className="row"><Button busy={up.busy} disabled={!subjectId || title.trim().length < 3} onClick={() => void upload()}>Validate & import</Button></div>
        </Card>
      )}
      <Card>
        <div className="card-head">
          <h2>Import history</h2>
          <SelectField label="Result" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All</option><option value="VALIDATED">Imported</option><option value="REJECTED">Rejected</option>
          </SelectField>
        </div>
        <p className="muted small">Imports run when the file is uploaded. An import either becomes a draft paper version (its status then shows review and publication) or is rejected with nothing imported.</p>
        {history.error && <Alert kind="error">{history.error} <Button small variant="secondary" onClick={history.reload}>Retry</Button></Alert>}
        {history.loading && !history.data ? <Spinner /> : (
          <>
            <DataTable rows={history.data?.items ?? []} rowKey={(u) => u.upload_id} empty={<EmptyState title="No imports yet" />}
              columns={[
                { header: "When", render: (u) => new Date(u.created_at).toLocaleString() },
                { header: "Title", render: (u) => u.title ?? "—" },
                { header: "Subject", render: (u) => sName(u.subject_id) },
                { header: "Result", render: (u) => <StatusBadge status={u.status === "VALIDATED" ? "SUCCESS" : "REJECTED"} /> },
                { header: "Questions", render: (u) => u.question_count ?? "—" },
                { header: "Answer key", render: (u) => (u.has_answer_key ? "Separate file" : "In the paper") },
                { header: "Paper version", render: (u) => (u.paper_version_id ? <><Link to={`/papers/${u.paper_version_id}`}>v{u.version_no}</Link> <StatusBadge status={u.paper_version_status ?? ""} /></> : "—") },
                { header: "Problems", render: (u) => (u.error_count ? <Button small variant="secondary" onClick={() => setExpanded(expanded === u.upload_id ? null : u.upload_id)}>{u.error_count} problem(s)</Button> : "—") },
              ]} />
            {expanded && (() => {
              const u = history.data?.items.find((x) => x.upload_id === expanded);
              return u ? (
                <Card>
                  <h3>Why “{u.title ?? "the upload"}” was rejected</h3>
                  <DataTable rows={u.errors} rowKey={(e) => `${e.row}-${e.field}-${e.message}`}
                    columns={[{ header: "Row / question", render: (e) => e.row ?? "—" }, { header: "Field", render: (e) => e.field ?? "—" }, { header: "What to fix", render: (e) => e.message }]} />
                  {u.error_count > u.errors.length && <p className="muted small">Showing the first {u.errors.length} of {u.error_count}.</p>}
                </Card>
              ) : null;
            })()}
            <Pagination page={page} pageSize={20} total={history.data?.total ?? 0} onChange={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}
