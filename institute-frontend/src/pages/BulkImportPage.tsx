import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useScope } from "../auth/ScopeContext";
import NeedBranch from "../components/NeedBranch";
import { ImportPreview, TaxonomyIssues } from "../components/bulkImportPreview";
import { ImportTemplateDownloads } from "../components/importTemplates";
import { Alert, Button, Card, ConfirmDialog, DataTable, EmptyState, PageHeader, Pagination, SelectField, Spinner, StatCard, StatusBadge } from "../components/ui";
import { useAction, useApi, useReference } from "../hooks/useApi";
import {
  ACCEPTED_EXTENSIONS, CANCELLABLE, FINISHED, JOB_STATUS_LABEL, MAX_UPLOAD_MB, WORKING, cancelImport, confirmImport, downloadReport, historyPath,
  jobPath, revalidate, targetsPath, uploadImport, type HistoryPage, type ImportJob, type ImportTargets, type ImportWhere, type Subject,
} from "../services/bulkImports";

const POLL_MS = 3000;           // how often a job the worker is processing is refreshed (only while it is processing)
const STEPS = ["Upload", "Preview", "Resolve", "Confirm", "Results"];

const fmt = (iso: string) => new Date(iso).toLocaleString();
const sizeText = (bytes: number) => (bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

/** Where an import's questions go, in words. */
const targetName = (job: ImportJob) => job.scope === "GLOBAL" ? "platform question bank" : job.branch_id ? "branch's question bank (shared with every branch)"
  : "institute's central question bank (provider-wide)";

/** The step the user is at, from the job's status (never stored in the browser). */
function currentStep(job: ImportJob | null): number {
  if (!job) return 0;
  if (FINISHED.includes(job.status) || ["IMPORT_QUEUED", "IMPORTING"].includes(job.status)) return 4;
  if (job.status === "PREVIEW") return job.invalid_rows > 0 ? 2 : 3;
  return 1;
}

function Steps({ active }: { active: number }) {
  return (
    <ol className="wizard-steps" aria-label="Import steps">
      {STEPS.map((s, i) => <li key={s} className={i === active ? "active" : i < active ? "done" : ""} aria-current={i === active ? "step" : undefined}>{s}</li>)}
    </ol>
  );
}

// ------------------------------------------------------------------ start page: where to, upload + history

type Option = { key: string; label: string; where?: Exclude<ImportWhere, { kind: "BRANCH" }>; subjects?: Subject[] };

/** The places the server says the user may import into (GET /bulk-imports/targets). A branch import is always offered (the server
 * still checks the branch); while the list loads, or if it cannot be loaded, only the branch import is shown. */
function importOptions(targets: ImportTargets | undefined): Option[] {
  const out: Option[] = [];
  if (targets?.global) out.push({ key: "GLOBAL", label: "Platform question bank (all institutes)", where: { kind: "GLOBAL" }, subjects: targets.global.subjects });
  for (const p of targets?.provider_wide ?? []) {
    out.push({ key: `PROVIDER:${p.provider_id}`, label: `Provider-wide — ${p.name} (shared with all its branches)`,
      where: { kind: "PROVIDER", providerId: p.provider_id }, subjects: p.subjects });
  }
  out.push({ key: "BRANCH", label: "A branch (its questions are shared with every branch of the institute)" });
  return out;
}

/** /papers/bulk-import: choose where to import, the subject and the file, and see earlier imports there. */
export default function BulkImportPage() {
  const targets = useApi<ImportTargets>(targetsPath);
  const options = importOptions(targets.data ?? undefined);
  const [key, setKey] = useState("BRANCH");
  const chosen = options.find((o) => o.key === key) ?? options[options.length - 1];
  return (
    <>
      <PageHeader crumbs={[<Link key="qb" to="/papers">Question Bank & Papers</Link>, "Bulk import"]} title="Bulk import questions"
        subtitle="Upload many questions at once from the Excel or Word template. You check a preview before anything is added to the Question Bank." />
      <Steps active={0} />
      {options.length > 1 && (
        <Card>
          <SelectField label="Import into" value={chosen.key} onChange={(e) => setKey(e.target.value)}>
            {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </SelectField>
        </Card>
      )}
      {chosen.where ? (
        <div className="stack">
          <UploadCard key={chosen.key} where={chosen.where} subjects={chosen.subjects} note={chosen.where.kind === "GLOBAL"
            ? "Platform questions are available to every institute. Chapters and topics are kept exactly as typed."
            : "The questions go to the institute's central Question Bank, shared with all its branches (no branch is recorded)."} />
          <ImportTemplateDownloads />
          <HistoryCard key={`h-${chosen.key}`} where={chosen.where} subjects={chosen.subjects} title={chosen.where.kind === "GLOBAL"
            ? "Import history — platform" : `Import history — ${chosen.label.split(" — ")[1]?.split(" (")[0] ?? "provider-wide"}`}
            emptyHint="Files you upload here are listed here." />
        </div>
      ) : (
        <NeedBranch>{({ providerId, branchId }) => <BranchImport key={branchId} providerId={providerId} branchId={branchId} />}</NeedBranch>
      )}
    </>
  );
}

function BranchImport({ providerId, branchId }: { providerId: string; branchId: string }) {
  const subjects = useReference<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const { branches } = useScope();
  const branchName = branches.find((b) => b.branch_id === branchId)?.name ?? "";
  const where: ImportWhere = { kind: "BRANCH", branchId };
  return (
    <div className="stack">
      <UploadCard where={where} subjects={subjects.data ?? undefined} subjectsError={subjects.error}
        note="The branch is the one selected above; its questions are shared with every branch of the institute." />
      <ImportTemplateDownloads />
      <HistoryCard where={where} subjects={subjects.data ?? undefined} title={`Import history${branchName ? ` — ${branchName}` : ""}`}
        emptyHint="Files you upload for this branch are listed here." />
    </div>
  );
}

/** Basic checks before sending (the server checks everything again and decides). */
function fileProblem(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) return "Choose an Excel (.xlsx) or Word (.docx) file made from the template. CSV and .doc files are not supported.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `The file is larger than ${MAX_UPLOAD_MB} MB. Split it into smaller files.`;
  if (file.size === 0) return "The file is empty.";
  return null;
}

function UploadCard({ where, subjects, subjectsError, note }: { where: ImportWhere; subjects?: Subject[]; subjectsError?: string | null; note: string }) {
  const navigate = useNavigate();
  const [subjectId, setSubjectId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const upload = useAction();

  const choose = (picked: File | null) => {
    setFile(picked);
    setProblem(picked ? fileProblem(picked) ?? "" : "");
  };
  const remove = () => {
    choose(null);
    if (input.current) input.current.value = "";
  };
  const missing = !subjectId ? "Choose the subject of these questions." : !file ? "Choose the file to upload." : problem || null;
  const start = () => upload.run(async () => {
    setProgress(0);
    try {
      const job = await uploadImport(where, subjectId, file!, setProgress);
      navigate(`/papers/bulk-import/${job.job_id}`);
    } finally {
      setProgress(null);
    }
  });

  return (
    <Card>
      <h2>1. Upload a file</h2>
      <p className="muted small">{note} Every question in the file is added to the subject you choose here.
        Accepted: Excel (.xlsx, up to 100,000 questions) or Word (.docx, up to 5,000), at most {MAX_UPLOAD_MB} MB, pictures included.</p>
      <div className="grid cols-2">
        <SelectField label="Subject" value={subjectId} onChange={(e) => setSubjectId(e.target.value)} required>
          <option value="">Choose a subject…</option>
          {(subjects ?? []).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
        </SelectField>
        <div className="field">
          <label htmlFor="bulk-import-file">File (.xlsx or .docx)</label>
          <input id="bulk-import-file" ref={input} type="file" accept={ACCEPTED_EXTENSIONS.join(",")}
            onChange={(e) => choose(e.target.files?.[0] ?? null)} />
        </div>
      </div>
      {subjectsError && <Alert kind="error">{subjectsError}</Alert>}
      {file && (
        <div className="row between selected-file">
          <span><strong>{file.name}</strong> <span className="muted">· {sizeText(file.size)}</span></span>
          <Button variant="secondary" small onClick={remove} disabled={upload.busy}>Remove</Button>
        </div>
      )}
      {problem && <Alert kind="error">{problem}</Alert>}
      {upload.error && <Alert kind="error">{upload.error}</Alert>}
      {progress !== null && (
        <div className="upload-progress">
          <progress max={1} value={progress} aria-label="Upload progress" /> <span className="small muted">{Math.round(progress * 100)}% uploaded</span>
        </div>
      )}
      <div className="row">
        <Button onClick={() => void start()} disabled={!!missing} busy={upload.busy}>Upload and check</Button>
        {missing && !problem && <span className="muted small">{missing}</span>}
      </div>
    </Card>
  );
}

function HistoryCard({ where, subjects, title, emptyHint }: { where: ImportWhere; subjects?: Subject[]; title: string; emptyHint: string }) {
  const [pageNo, setPageNo] = useState(1);
  const history = useApi<HistoryPage>(historyPath(where, pageNo));
  const subjectName = (id: string) => subjects?.find((s) => s.subject_id === id)?.name ?? "—";
  return (
    <Card>
      <div className="row between"><h2>{title}</h2>
        <Button variant="secondary" small onClick={history.reload}>Refresh</Button></div>
      {history.error && <Alert kind="error">{history.error}</Alert>}
      {history.loading && !history.data ? <Spinner /> : (
        <DataTable rows={history.data?.items ?? []} rowKey={(j) => j.job_id}
          empty={<EmptyState title="No imports yet" hint={emptyHint} />}
          columns={[
            { header: "File", render: (j) => <Link to={`/papers/bulk-import/${j.job_id}`}>{j.file_name}</Link> },
            { header: "Subject", render: (j) => subjectName(j.subject_id) },
            { header: "Uploaded", render: (j) => fmt(j.created_at) },
            { header: "Status", render: (j) => <StatusBadge status={j.status} /> },
            { header: "Questions", render: (j) => `${j.total_rows} read · ${j.valid_rows} ready · ${j.imported_rows} imported` },
          ]} />
      )}
      {history.data && <Pagination page={pageNo} pageSize={history.data.page_size} total={history.data.total} onChange={setPageNo} />}
    </Card>
  );
}

// ------------------------------------------------------------------ one import

/** /papers/bulk-import/:jobId: the import's progress, its preview and corrections, confirmation, and results. */
export function BulkImportJobPage() {
  const { jobId = "" } = useParams();
  const job = useApi<ImportJob>(jobPath(jobId));
  const data = job.data;
  const working = !!data && WORKING.includes(data.status);
  const { reload, loading } = job;

  // Refresh the status only while the worker is processing this job; stop as soon as it needs the user or has ended. The next
  // refresh is scheduled after EVERY completed one - also when nothing changed (a job still waiting for the worker), which the
  // status and progress alone would not show.
  useEffect(() => {
    if (!working || loading) return;
    const timer = window.setTimeout(reload, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [working, loading, reload]);

  const crumbs = [<Link key="qb" to="/papers">Question Bank & Papers</Link>, <Link key="bi" to="/papers/bulk-import">Bulk import</Link>, data?.file_name ?? "Import"];
  if (!data) {
    const notFound = job.error && /not found|404/i.test(job.error);
    return (
      <>
        <PageHeader crumbs={crumbs} title="Bulk import" />
        {job.error ? (
          <EmptyState title={notFound ? "This import is not available" : "The import could not be loaded"}
            hint={notFound ? "It does not exist, or you do not have access to it." : job.error}
            action={<Link className="btn secondary" to="/papers/bulk-import">Back to bulk import</Link>} />
        ) : <Spinner label="Loading the import" />}
      </>
    );
  }
  return (
    <>
      <PageHeader crumbs={crumbs} title={data.file_name}
        subtitle={`Uploaded ${fmt(data.created_at)} · ${data.file_format === "XLSX" ? "Excel" : "Word"} file · into the ${targetName(data)}`} />
      <Steps active={currentStep(data)} />
      <div className="stack">
        <JobSummary job={data} onChanged={reload} />
        {data.status === "PREVIEW" && <TaxonomyIssues job={data} onResolved={reload} />}
        {(data.status === "PREVIEW" || FINISHED.includes(data.status) || data.status === "IMPORTING" || data.status === "IMPORT_QUEUED")
          && data.total_rows > 0 && data.status !== "EXPIRED" && data.status !== "CANCELLED" && (
          <ImportPreview job={data} onRowChanged={reload} />
        )}
      </div>
    </>
  );
}

/** Status, counts and the actions the current status allows (the server still refuses anything it does not allow). */
function JobSummary({ job, onChanged }: { job: ImportJob; onChanged: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const act = useAction();
  const report = useAction();
  const skipped = job.invalid_rows + job.duplicate_rows;
  const finished = FINISHED.includes(job.status);

  const doConfirm = () => act.run(async () => {
    try {
      await confirmImport(job.job_id);
    } finally {
      setConfirming(false);
      onChanged();
    }
  });
  const doCancel = () => act.run(async () => {
    try {
      await cancelImport(job.job_id);
    } finally {
      setCancelling(false);
      onChanged();
    }
  });
  const doRevalidate = () => act.run(async () => { await revalidate(job.job_id); onChanged(); });

  return (
    <Card>
      <div className="row between">
        <h2><StatusBadge status={job.status} /> {JOB_STATUS_LABEL[job.status]}</h2>
        <div className="row">
          {job.status === "PREVIEW" && <Button variant="secondary" onClick={() => void doRevalidate()} busy={act.busy}>Check again</Button>}
          {CANCELLABLE.includes(job.status) && <Button variant="danger" onClick={() => setCancelling(true)}>Cancel import</Button>}
        </div>
      </div>
      <StatusMessage job={job} />
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {job.total_rows > 0 && (
        <div className="grid stats">
          <StatCard label="Questions read" value={job.total_rows} />
          {finished || job.confirmed_at ? <StatCard label="Imported" value={job.imported_rows} /> : <StatCard label="Ready to import" value={job.valid_rows} />}
          <StatCard label="Need correcting" value={job.invalid_rows} tone={job.invalid_rows ? "warn" : undefined} />
          <StatCard label="Duplicates (skipped)" value={job.duplicate_rows} />
          {job.failed_rows > 0 && <StatCard label="Failed" value={job.failed_rows} tone="danger" />}
        </div>
      )}
      {job.status === "PREVIEW" && (
        <div className="row">
          <Button onClick={() => setConfirming(true)} disabled={job.valid_rows === 0}>Upload {job.valid_rows} question{job.valid_rows === 1 ? "" : "s"} to the Question Bank</Button>
          {job.valid_rows === 0 && <span className="muted small">Correct at least one question first.</span>}
        </div>
      )}
      {(finished || job.status === "PREVIEW") && job.total_rows > 0 && (
        <div className="row">
          <Button variant="secondary" small busy={report.busy} onClick={() => void report.run(() => downloadReport(job.job_id))}>
            Download the problem report (CSV)</Button>
          <span className="muted small">Every row that is not (yet) imported, with the reason.</span>
        </div>
      )}
      {report.error && <Alert kind="error">{report.error}</Alert>}
      {confirming && (
        <ConfirmDialog title="Upload these questions?" confirmLabel={`Upload ${job.valid_rows} question${job.valid_rows === 1 ? "" : "s"}`}
          message={`${job.valid_rows} of ${job.total_rows} questions will be added to the Question Bank.`}
          busy={act.busy} onConfirm={() => void doConfirm()} onCancel={() => setConfirming(false)}>
          <ul className="small">
            <li>{job.invalid_rows} question(s) still need correcting and will be skipped.</li>
            <li>{job.duplicate_rows} duplicate(s) will be skipped.</li>
            <li>Skipped rows stay in the problem report. This cannot be undone from here: imported questions are managed in the Question Bank.</li>
          </ul>
        </ConfirmDialog>
      )}
      {cancelling && (
        <ConfirmDialog title="Cancel this import?" danger confirmLabel="Cancel import" busy={act.busy}
          message="The preview and its pictures are deleted. Nothing is added to the Question Bank." onConfirm={() => void doCancel()}
          onCancel={() => setCancelling(false)} />
      )}
      {skipped > 0 && job.status === "PREVIEW" && job.valid_rows > 0 && (
        <p className="muted small">You can upload the {job.valid_rows} ready question(s) now and correct the others later in a new file, or correct them below first.</p>
      )}
    </Card>
  );
}

function StatusMessage({ job }: { job: ImportJob }) {
  const problems = [...(job.file_errors ?? [])];
  if (job.last_error && !problems.includes(job.last_error)) problems.push(job.last_error);
  switch (job.status) {
    case "UPLOADED": case "PARSING": case "VALIDATE_QUEUED": case "VALIDATING":
      return <Alert kind="info">The file is being read and checked. This page updates by itself; large files can take a few minutes.
        {job.total_rows > 0 && ` ${job.total_rows} question(s) read so far.`}</Alert>;
    case "IMPORT_QUEUED": case "IMPORTING":
      return <Alert kind="info">Adding questions to the Question Bank: {job.progress.done} of {job.progress.total} done. You can leave this page.</Alert>;
    case "PREVIEW":
      return job.invalid_rows > 0
        ? <Alert kind="warn">{job.invalid_rows} question(s) need correcting. Fix them below (or resolve the chapter/topic names), or upload only the ready ones.</Alert>
        : <Alert kind="success">Every question passed the checks. Review the preview, then upload.</Alert>;
    case "COMPLETED":
      return <Alert kind="success">All {job.imported_rows} question(s) were added to the Question Bank.</Alert>;
    case "PARTIAL":
      return <Alert kind="warn">{job.imported_rows} question(s) were added. {job.invalid_rows + job.duplicate_rows + job.failed_rows} were not:
        see the problem report.</Alert>;
    case "FAILED":
      return <Alert kind="error">No question was imported.{problems.length ? ` ${problems.join(" ")}` : ""}</Alert>;
    case "CANCELLED":
      return <Alert kind="info">This import was cancelled. Nothing was added to the Question Bank.</Alert>;
    case "EXPIRED":
      return <Alert kind="info">This preview expired because it was not confirmed within 7 days; its files were deleted. Upload the file again to continue.</Alert>;
  }
}

