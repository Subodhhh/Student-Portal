import { useState } from "react";
import { Link } from "react-router-dom";
import { useScope } from "../auth/ScopeContext";
import ScopeBar from "../components/ScopeBar";
import { AudienceFields, PaperPicker } from "../components/testAudience";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, PageHeader, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { ApiError, api } from "../services/api";
import {
  STEPS, type AudienceState, type CheckError, type ScopeResponse, type ScheduledTest, type StepId, type SubjectReport, type UploadError,
  audienceBody, audienceReady, draftBody, emptyAudience, localToIso, newRequestKey, reportCsv, subjectsInAll, summarize,
} from "../services/createTest";

// Test -> Daily Test (formerly Create Test). The order is mandatory: audience, name, subjects, details & schedule, question papers (a new
// paper + answer key, or an approved paper from the bank), import & validate, review & publish. Every gate is decided by the server
// (scope, audience, metadata, the import itself, paper availability, publication); the page only collects input.

export default function CreateTestPage() {
  const scope = useScope();
  if (scope.loading) return <Spinner />;
  return (
    <>
      <PageHeader subtitle="A one-off test: choose who takes it, describe it, give each subject a new question paper and answer key or an approved paper from the bank, validate and publish." />
      <ScopeBar branch={false} />
      {!scope.providerId ? <EmptyState title="No provider available" hint="There is no provider you can create tests for." />
        : <Wizard key={scope.providerId} providerId={scope.providerId} />}
    </>
  );
}

function Errors({ errors }: { errors: CheckError[] }) {
  if (!errors.length) return null;
  return <Alert kind="error"><ul className="plain">{errors.map((e, i) => <li key={i}>{e.message}{e.value && e.field !== "subjects" ? ` (${e.value})` : ""}</li>)}</ul></Alert>;
}

function Wizard({ providerId }: { providerId: string }) {
  const sc = useApi<ScopeResponse>(`/api/v1/tests/scope?provider_id=${providerId}`);
  const [step, setStep] = useState<StepId>("audience");
  const [aud, setAud] = useState<AudienceState>(emptyAudience);
  const [eligible, setEligible] = useState<{ count: number; sample: { student_id: string; name: string | null }[]; key: string } | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [subjectIds, setSubjectIds] = useState<string[]>([]);
  const [meta, setMeta] = useState(() => {
    const start0 = new Date(Date.now() + 86400000);
    start0.setMinutes(0, 0, 0);
    return { duration: "60", start: toLocal(start0), end: toLocal(new Date(start0.getTime() + 3 * 3600000)),
      marking: "UNIFORM" as "UNIFORM" | "PER_QUESTION", correct: "4", wrong: "1", exam_type: "", target_class: "" };
  });
  const [files, setFiles] = useState<Record<string, { paper?: File; key?: File }>>({});
  const [source, setSource] = useState<Record<string, "upload" | "bank">>({});      // per subject; upload by default
  const [bankPaper, setBankPaper] = useState<Record<string, string>>({});            // subject -> chosen paper_id
  const [reports, setReports] = useState<Record<string, SubjectReport>>({});
  const [errors, setErrors] = useState<CheckError[]>([]);
  const [confirm, setConfirm] = useState(false);
  const [published, setPublished] = useState<ScheduledTest | null>(null);
  const [request, setRequest] = useState<{ key: string; body: string } | null>(null);
  const act = useAction();

  if (sc.loading && !sc.data) return <Spinner />;
  if (sc.error || !sc.data) return <Alert kind="error">{sc.error || "Could not load what you may target."}</Alert>;
  if (sc.data.branches.length === 0) return <EmptyState title="Nothing to target" hint="You have no branch in this provider where you may create tests." />;
  const scopeData = sc.data;
  const { branchIds, wholeIds, batchIds, students } = aud;
  const homeBranch = branchIds[0] ?? "";
  const chosenBranches = scopeData.branches.filter((b) => branchIds.includes(b.branch_id));
  const subjectsAllowed = subjectsInAll(scopeData, branchIds);
  const subjectName = (id: string) => subjectsAllowed.find((s) => s.subject_id === id)?.name ?? scopeData.branches.flatMap((b) => b.subjects).find((s) => s.subject_id === id)?.name ?? "Subject";
  const audience = audienceBody(providerId, branchIds, wholeIds, batchIds, [...students.keys()]);
  const fromBank = (sid: string) => source[sid] === "bank";
  const audienceKey = JSON.stringify(audience);
  const reportKey = (sid: string) => `${homeBranch}:${sid}`;
  const validated = subjectIds.length > 0 && subjectIds.every((sid) => reports[reportKey(sid)]?.ok);
  const body = draftBody({ audience, name, description, subjectIds, meta, versions: Object.fromEntries(subjectIds.map((sid) => [sid, reports[reportKey(sid)]?.paper_version_id ?? null])) });
  const idx = STEPS.findIndex((s) => s.id === step);

  // ---- local completeness of each step (the server then decides)
  const ready: Record<StepId, string | null> = {
    audience: audienceReady(aud),
    name: name.trim().length < 3 ? "Enter a test name (at least 3 characters)." : null,
    subjects: subjectIds.length === 0 ? "Select at least one subject." : null,
    details: !(Number(meta.duration) > 0) ? "Duration must be positive." : !meta.start || !meta.end ? "Choose when the window opens and closes."
      : new Date(meta.end) <= new Date(meta.start) ? "The window must close after it opens." : meta.marking === "UNIFORM" && !(Number(meta.correct) >= 1 && Number(meta.wrong) >= 0) ? "Enter the marks for correct and wrong answers." : null,
    files: validated ? null : subjectIds.some((sid) => (fromBank(sid) ? !reports[reportKey(sid)]?.ok : !files[sid]?.paper || !files[sid]?.key))
      ? "For every subject choose a question paper AND an answer key, or an approved paper from the bank." : null,
    validate: validated ? null : "Run Import & Validate until every subject passes.",
    review: null,
  };

  const go = (to: StepId) => { setErrors([]); setStep(to); };
  const failWith = (e: unknown) => {
    if (e instanceof ApiError && Array.isArray(e.extra.errors)) { setErrors(e.extra.errors as CheckError[]); throw new Error(e.message, { cause: e }); }
    throw e;
  };
  const next = () => act.run(async () => {
    setErrors([]);
    if (step === "audience") {
      try {
        const r = await api<{ eligible_count: number; students: { student_id: string; name: string | null }[] }>("/api/v1/tests/audience", { method: "POST", body: audience });
        if (r.eligible_count === 0) { setErrors([{ field: "audience", value: null, message: "Nobody is eligible: the selection has no active students." }]); return; }
        setEligible({ count: r.eligible_count, sample: r.students, key: audienceKey });
      } catch (e) { failWith(e); }
    }
    if (step === "details") {
      try { await api("/api/v1/tests/check", { method: "POST", body: draftBody({ audience, name, description, subjectIds, meta, versions: {} }) }); } catch (e) { failWith(e); }
    }
    go(STEPS[idx + 1].id);
  });

  // ---- audience changes drop subjects that are no longer available in every selected branch
  const changeAudience = (next: AudienceState) => {
    setAud(next);
    const allowed = new Set(subjectsInAll(scopeData, next.branchIds).map((x) => x.subject_id));
    setSubjectIds((ss) => ss.filter((x) => allowed.has(x)));
  };
  const toggle = (list: string[], set: (v: string[]) => void, id: string) => set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  const dropReport = (sid: string) => setReports((r) => { const n = { ...r }; delete n[reportKey(sid)]; return n; });
  const chooseBankPaper = (sid: string, paperId: string, versionId: string | null) => act.run(async () => {
    setBankPaper((b) => ({ ...b, [sid]: paperId }));
    if (!versionId) { dropReport(sid); return; }
    const rep = await summarize(sid, versionId, false);
    setReports((r) => ({ ...r, [reportKey(sid)]: { ...rep, fromBank: true } }));
  });

  // ---- import & validate (the paper service import: all or nothing; identical re-imports return the existing version)
  const importAll = () => act.run(async () => {
    setErrors([]);
    const out: Record<string, SubjectReport> = { ...reports };
    for (const sid of subjectIds) {
      const f = files[sid];
      if (fromBank(sid)) continue;                                                       // an approved bank paper needs no import
      if (out[reportKey(sid)]?.ok && !f?.paper) continue;
      if (!f?.paper || !f?.key) { out[reportKey(sid)] = { ok: false, subject_id: sid, message: "Both the question paper and the answer key are required.", errors: [], summary: null }; continue; }
      const form = new FormData();
      form.append("file", f.paper);
      form.append("answer_key", f.key);
      form.append("title", `${name.trim()} – ${subjectName(sid)}`);
      try {
        const r = await api<{ paper_version_id: string; question_count: number }>(`/api/v1/branches/${homeBranch}/subjects/${sid}/papers/upload`, { method: "POST", form });
        out[reportKey(sid)] = await summarize(sid, r.paper_version_id, false);
      } catch (e) {
        if (e instanceof ApiError && e.code === "DUPLICATE_PAPER" && typeof e.extra.paper_version_id === "string") {
          out[reportKey(sid)] = await summarize(sid, e.extra.paper_version_id, true);      // already imported: reuse, never duplicate
        } else if (e instanceof ApiError && e.code === "EXTRACTION_FAILED") {
          out[reportKey(sid)] = { ok: false, subject_id: sid, message: e.message, errors: (e.extra.errors as UploadError[]) ?? [], summary: (e.extra.summary as SubjectReport["summary"]) ?? null };
        } else {
          out[reportKey(sid)] = { ok: false, subject_id: sid, message: e instanceof Error ? e.message : "The import failed.", errors: [], summary: null };
        }
      }
    }
    setReports(out);
  });

  // ---- publish: papers through the normal approve/publish workflow, then one idempotent publication request
  const publish = () => act.run(async () => {
    setErrors([]);
    for (const sid of subjectIds) {
      const rep = reports[reportKey(sid)];
      if (!rep?.paper_version_id) throw new Error("A subject has not passed validation.");
      const pv = await api<{ latest_version: { status: string } }>(`/api/v1/paper-versions/${rep.paper_version_id}`);
      if (pv.latest_version.status === "VALIDATED") await api(`/api/v1/paper-versions/${rep.paper_version_id}/approve`, { method: "POST", body: { note: `Approved for test “${name.trim()}”` } });
      if (pv.latest_version.status !== "PUBLISHED") await api(`/api/v1/paper-versions/${rep.paper_version_id}/publish`, { method: "POST", body: { note: null } });
    }
    const json = JSON.stringify(body);
    const key = request && request.body === json ? request.key : newRequestKey();      // a retry of the SAME request reuses its key
    setRequest({ key, body: json });
    try {
      const t = await api<ScheduledTest>("/api/v1/tests", { method: "POST", body, headers: { "Idempotency-Key": key } });
      setPublished(t);
      setConfirm(false);
    } catch (e) {
      setConfirm(false);
      if (e instanceof ApiError && e.code === "PUBLICATION_FAILED") throw new Error(`${e.message} The test was NOT published; its state is recorded under Scheduled Tests.`, { cause: e });
      failWith(e);
    }
  });

  if (published) return <Published t={published} />;
  const audienceStale = eligible && eligible.key !== audienceKey;

  return (
    <div className="stack">
      <ol className="wizard-steps" aria-label="Steps">
        {STEPS.map((s, i) => <li key={s.id} aria-current={s.id === step ? "step" : undefined} className={i < idx ? "done" : undefined}>{i + 1}. {s.label}</li>)}
      </ol>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <Errors errors={errors} />

      {step === "audience" && (
        <Card>
          <h2>Who takes this test?</h2>
          <p className="muted small">Choose the branches first, then entire branches, batches and/or individual students. <strong>Individual students are added to the
            batches</strong> (they never narrow them); a student reached several ways is assigned once. The server decides who is eligible.</p>
          <AudienceFields providerId={providerId} scope={scopeData} value={aud} onChange={changeAudience} />
          {eligible && !audienceStale && <p><strong>{eligible.count}</strong> eligible student(s).</p>}
        </Card>
      )}

      {step === "name" && (
        <Card>
          <h2>Test name</h2>
          <Field label="Test name" value={name} maxLength={255} onChange={(e) => setName(e.target.value)} hint="3–255 characters; extra spaces are removed." />
          <TextAreaField label="Instructions for students (optional)" value={description} maxLength={4000} onChange={(e) => setDescription(e.target.value)} />
        </Card>
      )}

      {step === "subjects" && (
        <Card>
          <h2>Subjects</h2>
          <p className="muted small">Subjects available in every selected branch{scopeData.branches.length ? " for you" : ""}. A multi-subject test has one paper per subject and one shared timer.</p>
          {subjectsAllowed.length === 0 ? <EmptyState title="No subject is available in all selected branches" /> : (
            <div className="check-grid">{subjectsAllowed.map((s) => (
              <label key={s.subject_id}><input type="checkbox" checked={subjectIds.includes(s.subject_id)} onChange={() => toggle(subjectIds, setSubjectIds, s.subject_id)} /> {s.name}</label>
            ))}</div>
          )}
        </Card>
      )}

      {step === "details" && (
        <Card>
          <h2>Details & schedule</h2>
          <div className="grid cols-2">
            <Field label="Duration (minutes)" type="number" min={1} max={720} value={meta.duration} onChange={(e) => setMeta({ ...meta, duration: e.target.value })} />
            <SelectField label="Marking" value={meta.marking} onChange={(e) => setMeta({ ...meta, marking: e.target.value as "UNIFORM" | "PER_QUESTION" })}>
              <option value="UNIFORM">Same marks for every question</option><option value="PER_QUESTION">Marks from the question paper</option>
            </SelectField>
            {meta.marking === "UNIFORM" && <>
              <Field label="Marks for a correct answer" type="number" min={1} max={100} value={meta.correct} onChange={(e) => setMeta({ ...meta, correct: e.target.value })} />
              <Field label="Marks deducted for a wrong answer" type="number" min={0} max={100} value={meta.wrong} onChange={(e) => setMeta({ ...meta, wrong: e.target.value })} />
            </>}
            <Field label="Window opens" type="datetime-local" value={meta.start} onChange={(e) => setMeta({ ...meta, start: e.target.value })} />
            <Field label="Window closes (last time to start)" type="datetime-local" value={meta.end} onChange={(e) => setMeta({ ...meta, end: e.target.value })} />
            <Field label="Exam type (optional)" value={meta.exam_type} onChange={(e) => setMeta({ ...meta, exam_type: e.target.value })} />
            <Field label="Class / level (optional)" value={meta.target_class} onChange={(e) => setMeta({ ...meta, target_class: e.target.value })} />
          </div>
          <p className="muted small">Times are in your timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone}) and stored in UTC. Unanswered questions score 0. A student who
            starts before the window closes gets the full duration. Total marks are calculated from the validated papers.</p>
        </Card>
      )}

      {step === "files" && (
        <Card>
          <h2>Question papers</h2>
          <p className="muted small">For each subject either upload a new question paper with its answer key (both required), or choose an approved, published paper
            from the question bank. Question paper: CSV or one-sheet .xlsx (columns question, option_a–option_d, marks, negative_marks; optional question_id,
            explanation, topic), Word (.docx) or a text PDF; max 2 MB. Answer key: .csv/.txt, either <code>question_id,correct_option,explanation</code> (matched to
            the paper's question_id column) or <code>number,answer</code> lines such as <code>1,B</code> (matched by position). Quote explanations that contain commas.</p>
          {subjectIds.map((sid) => (
            <fieldset key={sid}><legend>{subjectName(sid)}{reports[reportKey(sid)]?.ok ? (fromBank(sid) ? " — approved paper chosen" : " — validated") : ""}</legend>
              <div className="row" role="radiogroup" aria-label={`Paper source for ${subjectName(sid)}`}>
                <label className="row"><input type="radio" name={`src-${sid}`} checked={!fromBank(sid)} onChange={() => { setSource((x) => ({ ...x, [sid]: "upload" })); dropReport(sid); }} /> Upload a new paper</label>
                <label className="row"><input type="radio" name={`src-${sid}`} checked={fromBank(sid)} onChange={() => { setSource((x) => ({ ...x, [sid]: "bank" })); dropReport(sid); setBankPaper((b) => ({ ...b, [sid]: "" })); }} /> Use an approved paper</label>
              </div>
              {fromBank(sid) ? (
                <PaperPicker providerId={providerId} subjectId={sid} label="Approved paper" value={bankPaper[sid] ?? ""} needPublished
                  onChange={(paper) => void chooseBankPaper(sid, paper?.paper_id ?? "", paper?.published_version?.paper_version_id ?? null)} />
              ) : (
                <div className="grid cols-2">
                  <div className="field"><label htmlFor={`paper-${sid}`}>Question paper</label>
                    <input id={`paper-${sid}`} type="file" accept=".csv,.xlsx,.docx,.pdf" onChange={(e) => { const file = e.target.files?.[0]; setFiles((f) => ({ ...f, [sid]: { ...f[sid], paper: file } })); dropReport(sid); }} /></div>
                  <div className="field"><label htmlFor={`key-${sid}`}>Answer key</label>
                    <input id={`key-${sid}`} type="file" accept=".csv,.txt" onChange={(e) => { const file = e.target.files?.[0]; setFiles((f) => ({ ...f, [sid]: { ...f[sid], key: file } })); dropReport(sid); }} /></div>
                </div>
              )}
            </fieldset>
          ))}
        </Card>
      )}

      {step === "validate" && (
        <Card>
          <div className="card-head"><h2>Import & validate</h2>
            <Button busy={act.busy} onClick={() => void importAll()}>Import & Validate</Button></div>
          <p className="muted small">Every question, option, answer and mark is checked. One problem rejects that subject's files completely (nothing is imported); more than five
            failed questions is reported as a rejection of the whole upload. Fix the files under “Question papers” and validate again. Approved papers chosen
            from the bank were validated when they were approved.</p>
          {subjectIds.map((sid) => <SubjectResult key={sid} name={subjectName(sid)} report={reports[reportKey(sid)]} />)}
          {Object.keys(reports).length > 0 && (
            <a className="btn secondary" download="validation-report.csv" href={`data:text/csv;charset=utf-8,${encodeURIComponent(reportCsv(subjectIds.map((sid) => ({ name: subjectName(sid), report: reports[reportKey(sid)] }))))}`}>Download the validation report</a>
          )}
        </Card>
      )}

      {step === "review" && (
        <Card>
          <h2>Review & publish</h2>
          <dl className="summary-grid">
            <dt>Test name</dt><dd>{name.trim()}</dd>
            <dt>Provider</dt><dd>{scopeData.provider_name}</dd>
            <dt>Branches</dt><dd>{chosenBranches.map((b) => b.name).join(", ")}</dd>
            <dt>Entire branches</dt><dd>{chosenBranches.filter((b) => wholeIds.includes(b.branch_id)).map((b) => b.name).join(", ") || "—"}</dd>
            <dt>Batches</dt><dd>{chosenBranches.flatMap((b) => b.batches).filter((x) => batchIds.includes(x.batch_id)).map((x) => x.name).join(", ") || "—"}</dd>
            <dt>Individual students</dt><dd>{[...students.values()].join(", ") || "—"}</dd>
            <dt>Eligible students</dt><dd>{audienceStale ? "recalculated at publication" : eligible?.count ?? "—"}</dd>
            <dt>Subjects</dt><dd>{subjectIds.map((sid) => `${subjectName(sid)} (${reports[reportKey(sid)]?.question_count ?? "?"} questions)`).join(", ")}</dd>
            <dt>Questions</dt><dd>{subjectIds.reduce((n, sid) => n + (reports[reportKey(sid)]?.question_count ?? 0), 0)}</dd>
            <dt>Total marks</dt><dd>{meta.marking === "UNIFORM" ? subjectIds.reduce((n, sid) => n + (reports[reportKey(sid)]?.question_count ?? 0), 0) * Number(meta.correct) : subjectIds.reduce((n, sid) => n + Number(reports[reportKey(sid)]?.total_marks ?? 0), 0)}</dd>
            <dt>Marking</dt><dd>{meta.marking === "UNIFORM" ? `+${meta.correct} correct, −${meta.wrong} wrong, 0 unanswered` : "As in the question papers"}</dd>
            <dt>Duration</dt><dd>{meta.duration} minutes</dd>
            <dt>Window</dt><dd>{new Date(meta.start).toLocaleString()} – {new Date(meta.end).toLocaleString()} (UTC {localToIso(meta.start).slice(0, 16)} – {localToIso(meta.end).slice(0, 16)})</dd>
          </dl>
          <div className="row" style={{ marginTop: "var(--space-4)" }}><Button disabled={!validated} onClick={() => setConfirm(true)}>Publish test</Button></div>
        </Card>
      )}

      <div className="row between">
        <Button variant="secondary" disabled={idx === 0 || act.busy} onClick={() => go(STEPS[idx - 1].id)}>Back</Button>
        {step !== "review" && (
          <span className="row">{ready[step] && <span className="muted small">{ready[step]}</span>}
            <Button busy={act.busy} disabled={!!ready[step]} onClick={() => void next()}>Next</Button></span>
        )}
      </div>
      {confirm && (
        <ConfirmDialog title="Publish this test?" message={`The test is assigned to the eligible students and delivered to them; it opens ${new Date(meta.start).toLocaleString()}.`}
          confirmLabel="Publish" busy={act.busy} onConfirm={() => void publish()} onCancel={() => setConfirm(false)} />
      )}
    </div>
  );
}

function SubjectResult({ name, report }: { name: string; report?: SubjectReport }) {
  if (!report) return <p className="muted">{name}: not validated yet.</p>;
  const s = report.summary;
  return (
    <section className="stack" aria-label={`Validation of ${name}`}>
      <h3>{name}: <span className={report.ok ? "report-ok" : "report-bad"}>{report.ok ? "Passed" : "Rejected"}</span></h3>
      {report.ok ? (
        <p className="small">{report.question_count} questions{report.fromBank ? " in the approved paper chosen from the bank" : " extracted and valid"} · total marks {report.total_marks}{report.reused ? " · identical paper already imported, reused (no duplicate)" : ""}.</p>
      ) : (
        <>
          <p className="small">{report.message}</p>
          {s && <p className="small">Questions detected: {s.questions_detected} · valid: {s.questions_valid} · failed: {s.questions_failed}{s.threshold_exceeded ? " · more than five failed: whole upload rejected" : ""}</p>}
          {report.errors.length > 0 && (
            <DataTable rows={report.errors} rowKey={(e) => `${e.row}-${e.field}-${e.message}`}
              columns={[{ header: "Question", render: (e) => e.question ?? "—" }, { header: "Row", render: (e) => e.row ?? "—" },
                { header: "Field", render: (e) => e.field ?? "—" }, { header: "Problem", render: (e) => e.message }]} />
          )}
        </>
      )}
    </section>
  );
}

function Published({ t }: { t: ScheduledTest }) {
  return (
    <Card>
      <h2>Daily Test published</h2>
      <p><StatusBadge status={t.state} /> {t.state === "PUBLISHED_DELIVERY_PENDING" ? "Assigned to the students; delivery to the student portal is in progress (it is retried automatically)." : "Delivered to the student portal."}</p>
      <dl className="summary-grid">
        <dt>Test</dt><dd>{t.name}</dd>
        <dt>Window</dt><dd>{new Date(t.start_at).toLocaleString()} – {new Date(t.end_at).toLocaleString()}</dd>
        <dt>Subjects</dt><dd>{t.subjects.join(", ")}</dd>
        <dt>Questions / marks</dt><dd>{t.question_count} / {t.total_marks}</dd>
        <dt>Assigned students</dt><dd>{t.assigned_students}</dd>
      </dl>
      <div className="row">{t.exam_id && <Link className="btn" to={`/exams/${t.exam_id}`}>Open the scheduled test</Link>}<Link className="btn secondary" to="/tests">Scheduled Tests</Link></div>
    </Card>
  );
}

function toLocal(d: Date) {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
