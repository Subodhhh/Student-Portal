import { API_URL, api, downloadFile, uploadWithProgress, type Page } from "./api";

/**
 * Bulk question import: every call the Bulk Import page makes, in one place. The shapes mirror the paper service
 * (inst_paper/routes/bulk_imports.py). The server decides everything that matters - who may see a job, what is valid, whether a job
 * can be confirmed - so this file only sends requests and describes responses; it never re-implements those rules.
 */

export type JobStatus =
  | "UPLOADED" | "PARSING" | "VALIDATE_QUEUED" | "VALIDATING" | "PREVIEW" | "IMPORT_QUEUED" | "IMPORTING"
  | "COMPLETED" | "PARTIAL" | "FAILED" | "CANCELLED" | "EXPIRED";
export type RowStatus = "PENDING" | "VALID" | "INVALID" | "DUPLICATE" | "IMPORTED" | "FAILED";

/** GLOBAL: the platform bank (no provider, no branch). PROVIDER: a provider's bank - provider-wide when branch_id is null. */
export type ImportJob = {
  job_id: string; scope: "GLOBAL" | "PROVIDER"; provider_id: string | null; branch_id: string | null; subject_id: string; status: JobStatus; file_format: "XLSX" | "DOCX";
  file_name: string; total_rows: number; valid_rows: number; invalid_rows: number; duplicate_rows: number; imported_rows: number;
  failed_rows: number; progress: { done: number; total: number }; file_errors: string[] | null; last_error: string | null;
  created_by: string; created_at: string; confirmed_by: string | null; confirmed_at: string | null; finished_at: string | null;
};

/** The template's text columns, as the server stores and accepts them. */
export type RowData = {
  chapter: string; topic: string; subtopic: string; difficulty: string; question_type: string; question_text: string;
  option_a_text: string; option_b_text: string; option_c_text: string; option_d_text: string;
  correct_option: string;               // the Correct Answer of every type: an MCQ letter, MSQ letters ("A, C") or a NAT number
  // one template for every type: MSQ options E-H and the NAT columns (present when the file has those columns)
  option_e_text?: string; option_f_text?: string; option_g_text?: string; option_h_text?: string;
  tolerance_type?: string; tolerance?: string; minimum?: string; maximum?: string; answer_format?: string; decimal_places?: string; unit?: string;
};
export type ImageColumn = "question_image" | `option_${"a" | "b" | "c" | "d" | "e" | "f" | "g" | "h"}_image`;
/** A record a typed name may mean: HIGH (very likely) or MEDIUM; `matched`: the name or alias it resembles. */
export type Suggestion = { node_id: string; name: string; score?: number; band?: "HIGH" | "MEDIUM"; matched?: string;
  parent_id?: string | null; parent_name?: string | null };
export type RowError = { field: string | null; message: string; code?: string; level?: string; value?: string; parent_id?: string | null;
  suggestions?: Suggestion[] };
export type ImportRow = {
  row_no: number; status: RowStatus; data: RowData; errors: RowError[];
  label: string;                                        // how to find the row in the file (its row number)
  question_type: "MCQ_SINGLE" | "MCQ_MULTI" | "NUMERICAL"; import_anyway: boolean;
  images: Partial<Record<ImageColumn, { sha256: string; url: string }>>;
  content_format: "TEXT" | "IMAGE" | "TEXT_WITH_IMAGE"; question_id: string | null;
};
export type TaxonomyLevel = "CHAPTER" | "TOPIC" | "SUBTOPIC";
export type TaxonomyIssue = { level: TaxonomyLevel; value: string; parent_id: string | null; suggestions: Suggestion[]; rows: number;
  example_rows: number[];
  preselect?: string | null;        // the one clearly meant: shown chosen, still confirmed by the person
  elsewhere?: Suggestion[];         // the same name at another place of the tree
  may_manage?: boolean;             // the caller may approve aliases and create records (otherwise: map, and request)
};
export type TaxonomyNode = { node_id: string; parent_id: string | null; level: TaxonomyLevel; name: string; status: string };
export type Resolution = { level: TaxonomyLevel; value: string; parent_id: string | null; action: "MAP" | "ALIAS" | "CREATE"; node_id?: string;
  remember?: boolean };

/** Files the server accepts, and its default size limit (BULK_IMPORT_MAX_BYTES). Checked here only to give an early message. */
export const ACCEPTED_EXTENSIONS = [".xlsx", ".docx"];
export const MAX_UPLOAD_MB = 2048;
export const MAX_UPLOAD_TEXT = MAX_UPLOAD_MB >= 1024 ? `${MAX_UPLOAD_MB / 1024} GB` : `${MAX_UPLOAD_MB} MB`;

/** Jobs the worker is still working on: the page refreshes their status while they are in one of these states. */
export const WORKING: JobStatus[] = ["UPLOADED", "PARSING", "VALIDATE_QUEUED", "VALIDATING", "IMPORT_QUEUED", "IMPORTING"];
export const FINISHED: JobStatus[] = ["COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "EXPIRED"];
export const CANCELLABLE: JobStatus[] = ["UPLOADED", "PREVIEW", "VALIDATE_QUEUED"];

/** Plain-language names for job states (shown to teachers). */
export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  UPLOADED: "Waiting to be read", PARSING: "Reading the file", VALIDATE_QUEUED: "Waiting to be checked", VALIDATING: "Checking questions",
  PREVIEW: "Ready for review", IMPORT_QUEUED: "Waiting to import", IMPORTING: "Importing", COMPLETED: "Completed",
  PARTIAL: "Partially completed", FAILED: "Failed", CANCELLED: "Cancelled", EXPIRED: "Expired",
};

const base = "/api/v1/bulk-imports";

export type Subject = { subject_id: string; name: string };
/** What the caller may import into, decided by the server (GET /bulk-imports/targets); the page offers only these. */
export type ImportTargets = {
  global: { subjects: Subject[] } | null;
  provider_wide: { provider_id: string; name: string; subjects: Subject[] }[];
  branch: boolean;
};
/** Where one upload goes. A branch's questions are still shared with every branch of its provider. */
export type ImportWhere = { kind: "BRANCH"; branchId: string } | { kind: "PROVIDER"; providerId: string } | { kind: "GLOBAL" };
export const targetsPath = `${base}/targets`;

// ------------------------------------------------------------------ resumable uploads (routes/bulk_uploads.py)

/** A file being sent in pieces. The server says how much has arrived, so an interrupted upload continues from there. */
export type UploadSession = { upload_id: string; scope: "GLOBAL" | "PROVIDER"; provider_id: string | null; subject_id: string;
  file_name: string; size: number; chunk_size: number; received_parts: number; received_bytes: number;
  status: "RECEIVING" | "COMPLETED" | "DISCARDED"; job_id: string | null; created_at: string; updated_at: string; resumed?: boolean };
export type UploadWhere = Exclude<ImportWhere, { kind: "BRANCH" }>;
export const uploadsPath = `${base}/uploads`;
/** The same file chosen again (same name, size and date) is recognised by this and continues where it stopped. */
export const fingerprintOf = (f: File) => `${f.name}|${f.size}|${f.lastModified}`.slice(0, 300);
export const startUpload = (where: UploadWhere, subjectId: string, file: File) => api<UploadSession>(uploadsPath, { method: "POST", body: {
  subject_id: subjectId, target: where.kind, provider_id: where.kind === "PROVIDER" ? where.providerId : null, file_name: file.name,
  size: file.size, fingerprint: fingerprintOf(file) } });
export const sendPart = (uploadId: string, n: number, piece: Blob, onProgress: (fraction: number) => void) =>
  uploadWithProgress<UploadSession>(`${uploadsPath}/${uploadId}/parts/${n}`, piece, onProgress, "PUT");
export const completeUpload = (uploadId: string) => api<ImportJob>(`${uploadsPath}/${uploadId}/complete`, { method: "POST" });
export const discardUpload = (uploadId: string) => api<UploadSession>(`${uploadsPath}/${uploadId}`, { method: "DELETE" });
export const historyPath = (where: ImportWhere, page: number) => {
  const of = where.kind === "BRANCH" ? `branch_id=${encodeURIComponent(where.branchId)}`
    : where.kind === "PROVIDER" ? `provider_id=${encodeURIComponent(where.providerId)}` : "target=GLOBAL";
  return `${base}?${of}&page=${page}&page_size=10`;
};
export const jobPath = (jobId: string) => `${base}/${encodeURIComponent(jobId)}`;
export const rowsPath = (jobId: string, status: string, page: number) =>
  `${jobPath(jobId)}/rows?page=${page}&page_size=25${status ? `&status=${status}` : ""}`;
export const taxonomyIssuesPath = (jobId: string) => `${jobPath(jobId)}/taxonomy-issues`;
/** Every record of the subject (all levels), to name parents and offer records to map to. No provider: the platform list. */
export const taxonomyNodesPath = (providerId: string | null, subjectId: string) =>
  `/api/v1/question-taxonomy/nodes?subject_id=${encodeURIComponent(subjectId)}${providerId ? `&provider_id=${encodeURIComponent(providerId)}` : ""}`;

/** A preview picture's address: the authenticated API endpoint the server returned (never a storage URL built here). */
export const imageSrc = (url: string) => `${API_URL}${url}`;

export const correctRow = (jobId: string, rowNo: number, changes: Partial<RowData>) =>
  api<ImportRow>(`${jobPath(jobId)}/rows/${rowNo}`, { method: "PATCH", body: changes });
/** R1: a KEY_CONFLICT row is imported as a SEPARATE question; the existing question and its key are never changed (audited). */
export const importAnyway = (jobId: string, rowNo: number) =>
  api<ImportRow>(`${jobPath(jobId)}/rows/${rowNo}/import-anyway`, { method: "POST" });
export const resolveTaxonomy = (jobId: string, resolution: Resolution) =>
  api<ImportJob>(`${jobPath(jobId)}/taxonomy-resolutions`, { method: "POST", body: resolution });
export const revalidate = (jobId: string) => api<ImportJob>(`${jobPath(jobId)}/revalidate`, { method: "POST" });
export const confirmImport = (jobId: string) => api<ImportJob>(`${jobPath(jobId)}/confirm`, { method: "POST" });
export const cancelImport = (jobId: string) => api<ImportJob>(`${jobPath(jobId)}/cancel`, { method: "POST" });
export const downloadReport = (jobId: string) => downloadFile(`${jobPath(jobId)}/report.csv`, "import-report.csv");

export type HistoryPage = Page<ImportJob>;
