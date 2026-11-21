import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { MathText } from "./MathText";
import { Alert, Button, Card, ConfirmDialog, EmptyState, Field, Modal, Pagination, SelectField, Spinner, StatusBadge, Tabs, TextAreaField } from "./ui";
import { TypeBadge } from "./questionBank";
import { useAction, useApi } from "../hooks/useApi";
import {
  correctRow, imageSrc, importAnyway, resolveTaxonomy, rowsPath, taxonomyIssuesPath, taxonomyNodesPath, type ImageColumn, type ImportJob, type ImportRow,
  type RowData, type TaxonomyIssue, type TaxonomyNode,
} from "../services/bulkImports";
import type { Page } from "../services/api";

const OPTIONS = ["a", "b", "c", "d"] as const;
const MSQ_OPTIONS = ["a", "b", "c", "d", "e", "f", "g", "h"] as const;
/** A Question Type value as the server reads it (bulk_validation.row_type): MSQ / NAT and their usual spellings, otherwise MCQ. */
const typeOfWord = (word: string) => {
  const w = (word ?? "").trim().toUpperCase().replace(/[\s-]+/g, "_");
  return ["MSQ", "MCQ_MULTIPLE", "MCQ_MULTI", "MULTIPLE_CORRECT"].includes(w) ? "MCQ_MULTI"
    : ["NAT", "NUMERICAL", "NUMERIC", "INTEGER"].includes(w) ? "NUMERICAL" : "MCQ_SINGLE";
};
const TOLERANCE_LABEL: Record<string, string> = { EXACT: "exactly", PLUS_MINUS: "±", "PLUS-MINUS": "±", RANGE: "range" };
const FORMAT_LABEL = { TEXT: "Text", IMAGE: "Picture", TEXT_WITH_IMAGE: "Text and picture" };
const LEVEL_LABEL = { CHAPTER: "Chapter", TOPIC: "Topic", SUBTOPIC: "Subtopic" };

/** One picture of the preview, from the authenticated API (the session cookie authorises it; the bucket stays private). */
function Picture({ image, alt }: { image?: { sha256: string; url: string }; alt: string }) {
  if (!image) return null;
  return <img className="import-thumb" src={imageSrc(image.url)} alt={alt} loading="lazy" />;
}

/** The questions of an import, page by page, filtered by status. Never more than one page is in the browser. */
export function ImportPreview({ job, onRowChanged }: { job: ImportJob; onRowChanged: () => void }) {
  const [status, setStatus] = useState("");
  const [pageNo, setPageNo] = useState(1);
  const [editing, setEditing] = useState<ImportRow | null>(null);
  // the job's status and counts are part of the key: a revalidation or import refreshes the page shown
  const rows = useApi<Page<ImportRow>>(`${rowsPath(job.job_id, status, pageNo)}&v=${job.status}-${job.valid_rows}-${job.invalid_rows}-${job.imported_rows}`);
  const editable = job.status === "PREVIEW";
  const tabs = editable
    ? [{ id: "", label: `All (${job.total_rows})` }, { id: "INVALID", label: `Need correcting (${job.invalid_rows})` },
      { id: "DUPLICATE", label: `Duplicates (${job.duplicate_rows})` }, { id: "VALID", label: `Ready (${job.valid_rows})` }]
    : [{ id: "", label: "All" }, { id: "IMPORTED", label: `Imported (${job.imported_rows})` }, { id: "INVALID", label: "Not imported: invalid" },
      { id: "DUPLICATE", label: "Duplicates" }, { id: "FAILED", label: `Failed (${job.failed_rows})` }];
  return (
    <Card>
      <h2>Preview</h2>
      <Tabs tabs={tabs} active={status} onChange={(id) => { setStatus(id); setPageNo(1); }} />
      {rows.error && <Alert kind="error">{rows.error}</Alert>}
      {rows.loading && !rows.data ? <Spinner label="Loading questions" /> : (rows.data?.items.length ?? 0) === 0 ? (
        <EmptyState title="No questions here" hint={status ? "No question has this status." : "The file had no questions."} />
      ) : (
        <ol className="import-rows">
          {rows.data!.items.map((row) => <RowCard key={row.row_no} row={row} onEdit={editable && row.status !== "IMPORTED" ? () => setEditing(row) : undefined}
            jobId={editable ? job.job_id : undefined} onChanged={() => { rows.reload(); onRowChanged(); }} />)}
        </ol>
      )}
      {rows.data && <Pagination page={pageNo} pageSize={rows.data.page_size} total={rows.data.total} onChange={setPageNo} />}
      {editing && <RowEditor jobId={job.job_id} row={editing} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); rows.reload(); onRowChanged(); }} />}
    </Card>
  );
}

function RowCard({ row, onEdit, jobId, onChanged }: { row: ImportRow; onEdit?: () => void; jobId?: string; onChanged: () => void }) {
  const d = row.data;
  const type = row.question_type ?? "MCQ_SINGLE";
  const label = row.label ?? String(row.row_no);
  // the letter(s) as the server reads them ("A", "(A)", "Option A"; MSQ "A, C"); display only - the server decides validity
  const correct = type === "MCQ_MULTI" ? (d.correct_option ?? "").toUpperCase().split(/[\s,;]+/).filter(Boolean)
    : [/^\(?\s*(?:option\s*)?([a-d])\s*\)?$/i.exec((d.correct_option ?? "").trim())?.[1].toUpperCase() ?? ""].filter(Boolean);
  const options = type === "MCQ_MULTI" ? MSQ_OPTIONS.filter((x, i) => i < 2 || d[`option_${x}_text` as keyof RowData] || row.images[`option_${x}_image` as ImageColumn]) : OPTIONS;
  const problem = row.status === "INVALID" || row.status === "FAILED";
  const taxonomy = row.errors.some((e) => e.code === "TAXONOMY_UNMATCHED");
  const conflict = row.status === "INVALID" && row.errors.length > 0 && row.errors.every((e) => e.code === "KEY_CONFLICT");
  const [asking, setAsking] = useState(false);
  const anyway = useAction();
  return (
    <li className={`import-row${problem ? " problem" : ""}${row.status === "DUPLICATE" ? " duplicate" : ""}`} aria-label={`Row ${label}`}>
      <div className="row between">
        <span className="small"><strong>Row {label}</strong> <TypeBadge type={type} /> <StatusBadge status={row.status} />
          {taxonomy && <span className="badge warn">Chapter/topic to resolve</span>}
          {row.import_anyway && <span className="badge info">Import anyway: a separate question</span>}</span>
        <span className="row nowrap">
          {conflict && jobId && <Button variant="secondary" small onClick={() => setAsking(true)}>Import anyway</Button>}
          {onEdit && <Button variant="secondary" small onClick={onEdit}>Correct</Button>}
        </span>
      </div>
      {d.question_text ? <MathText text={d.question_text} as="p" /> : !row.images.question_image && <p className="muted">(no question text)</p>}
      <Picture image={row.images.question_image} alt={`Row ${label}: question picture`} />
      {type === "NUMERICAL" ? (
        <p className="small">Answer: <strong>{d.correct_option || "(empty)"}</strong>{d.unit ? ` ${d.unit}` : ""}
          {" · "}{TOLERANCE_LABEL[(d.tolerance_type ?? "").toUpperCase()] ?? d.tolerance_type ?? "no tolerance type"}
          {d.tolerance ? ` ${d.tolerance}` : ""}{d.minimum || d.maximum ? ` ${d.minimum || "?"} to ${d.maximum || "?"}` : ""}
          {" · "}{d.answer_format || "no answer format"}{d.decimal_places ? `, ${d.decimal_places} decimal places` : ""}</p>
      ) : (
      <ol type="A" className="import-options">
        {options.map((x) => {
          const image = row.images[`option_${x}_image` as ImageColumn];
          const text = d[`option_${x}_text` as keyof RowData];
          return (
            <li key={x} className={correct.includes(x.toUpperCase()) ? "correct" : undefined}>
              {text ? <MathText text={text} /> : !image && <span className="muted">(empty)</span>}
              <Picture image={image} alt={`Row ${label}: option ${x.toUpperCase()} picture`} />
              {correct.includes(x.toUpperCase()) && <span className="small"> ✓ correct</span>}
            </li>
          );
        })}
      </ol>
      )}
      <p className="small muted">
        {[d.chapter || "no chapter", d.topic, d.subtopic].filter(Boolean).join(" › ")} · {d.difficulty || "no difficulty"}
        {" · "}{d.question_type || "no type"}
        {" · "}{FORMAT_LABEL[row.content_format]}{type !== "NUMERICAL" && !correct.length && " · no correct option"}
      </p>
      {row.errors.length > 0 && (
        <ul className={`small ${problem ? "error-list" : "muted"}`}>
          {row.errors.map((e, i) => <li key={i}>{e.field ? <strong>{e.field.replace(/_/g, " ")}: </strong> : null}{e.message}</li>)}
        </ul>
      )}
      {asking && jobId && (
        <ConfirmDialog title={`Import row ${label} anyway?`} confirmLabel="Import as a separate question" busy={anyway.busy}
          message="This row is added as a NEW question beside the existing one, which keeps its answer key unchanged. Your decision is recorded."
          onCancel={() => setAsking(false)} onConfirm={() => void anyway.run(async () => { await importAnyway(jobId, row.row_no); setAsking(false); onChanged(); })}>
          {anyway.error && <Alert kind="error">{anyway.error}</Alert>}
        </ConfirmDialog>
      )}
    </li>
  );
}

/** Correct a row's text. Only the fields the server accepts are offered; pictures are changed by uploading the file again. */
function RowEditor({ jobId, row, onClose, onSaved }: { jobId: string; row: ImportRow; onClose: () => void; onSaved: () => void }) {
  const [values, setValues] = useState<RowData>(row.data);
  const save = useAction();
  const set = (k: keyof RowData) => (e: { target: { value: string } }) => setValues((v) => ({ ...v, [k]: e.target.value }));
  const changed = (Object.keys(values) as (keyof RowData)[]).filter((k) => values[k] !== row.data[k]);
  const submit = () => save.run(async () => {
    await correctRow(jobId, row.row_no, Object.fromEntries(changed.map((k) => [k, values[k]])));
    onSaved();
  });
  const type = typeOfWord(values.question_type);          // the fields follow the type being typed (as the server reads it)
  const text = (k: keyof RowData, label: string, hint?: string) => <Field key={k} label={label} value={values[k] ?? ""} onChange={set(k)} hint={hint} />;
  return (
    <Modal title={`Correct row ${row.label ?? row.row_no}`} onClose={onClose}>
      {row.errors.length > 0 && <Alert kind="warn"><ul className="plain">{row.errors.map((e, i) => <li key={i}>{e.message}</li>)}</ul></Alert>}
      <div className="grid cols-3">
        <Field label="Chapter" value={values.chapter} onChange={set("chapter")} />
        <Field label="Topic" value={values.topic} onChange={set("topic")} />
        <Field label="Subtopic" value={values.subtopic} onChange={set("subtopic")} />
        <SelectField label="Difficulty" value={values.difficulty} onChange={set("difficulty")}>
          {!["Easy", "Medium", "Hard", "EASY", "MEDIUM", "HARD"].includes(values.difficulty) && <option value={values.difficulty}>{values.difficulty || "Choose…"}</option>}
          <option value="Easy">Easy</option><option value="Medium">Medium</option><option value="Hard">Hard</option>
        </SelectField>
        <Field label="Question type" value={values.question_type} onChange={set("question_type")} hint="MCQ, MSQ or NAT" />
        <Field label="Correct answer" value={values.correct_option} onChange={set("correct_option")}
          hint={type === "MCQ_MULTI" ? "Every correct letter, e.g. A, C" : type === "NUMERICAL" ? "A number, digits only, e.g. 9.8" : "A, B, C or D"} />
        {type === "NUMERICAL" && <>
          {text("tolerance_type", "Tolerance type", "Exact, Plus-minus or Range")}
          {text("tolerance", "Tolerance", "For Plus-minus")}
          {text("minimum", "Minimum", "For Range")}
          {text("maximum", "Maximum", "For Range")}
          {text("answer_format", "Answer format", "Integer or Decimal")}
          {text("decimal_places", "Decimal places", "0 to 6")}
          {text("unit", "Unit", "Optional, display only")}</>}
      </div>
      <TextAreaField label="Question text" value={values.question_text} onChange={set("question_text")} />
      {type !== "NUMERICAL" && (
        <div className="grid cols-2">
          {(type === "MCQ_MULTI" ? MSQ_OPTIONS : OPTIONS).map((x) => (
            <Field key={x} label={`Option ${x.toUpperCase()} text`} value={values[`option_${x}_text` as keyof RowData] ?? ""} onChange={set(`option_${x}_text` as keyof RowData)}
              hint={row.images[`option_${x}_image` as ImageColumn] ? "This option is a picture; leave the text empty." : undefined} />
          ))}
        </div>
      )}
      {save.error && <Alert kind="error">{save.error}</Alert>}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={() => void submit()} busy={save.busy} disabled={changed.length === 0}>Save and check again</Button>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ chapter / topic / subtopic names that match nothing

/** Unmatched taxonomy values with the server's suggestions. Administrators with taxonomy.manage choose what each value means; the
 * page never maps a similar name by itself. Everyone else sees what must be fixed and who can do it. */
export function TaxonomyIssues({ job, onResolved }: { job: ImportJob; onResolved: () => void }) {
  const { can } = useAuth();
  // platform (GLOBAL) imports keep chapters and topics as typed: there is no platform taxonomy to resolve against
  const issues = useApi<TaxonomyIssue[]>(job.invalid_rows > 0 && job.scope !== "GLOBAL" ? `${taxonomyIssuesPath(job.job_id)}?v=${job.invalid_rows}` : null);
  const manager = can("taxonomy.manage");
  const nodes = useApi<TaxonomyNode[]>(manager && job.provider_id && (issues.data?.length ?? 0) > 0 ? taxonomyNodesPath(job.provider_id, job.subject_id) : null);
  if (!issues.data?.length) return issues.error ? <Alert kind="error">{issues.error}</Alert> : null;
  const nameOf = (id: string | null) => nodes.data?.find((n) => n.node_id === id)?.name;
  return (
    <Card>
      <h2>Chapters, topics and subtopics to resolve</h2>
      <p className="muted small">These names match nothing set up for this subject. Similar names are only suggestions; nothing is changed until
        {manager ? " you choose what each name means." : " an administrator resolves them. You can also correct the rows instead."}</p>
      {nodes.error && <Alert kind="error">{nodes.error}</Alert>}
      <ul className="import-issues">
        {issues.data.map((issue) => (
          <IssueItem key={`${issue.level}|${issue.parent_id}|${issue.value}`} job={job} issue={issue} manager={manager}
            parentName={nameOf(issue.parent_id)} nodes={nodes.data ?? []} onResolved={onResolved} />
        ))}
      </ul>
    </Card>
  );
}

function IssueItem({ job, issue, manager, parentName, nodes, onResolved }: {
  job: ImportJob; issue: TaxonomyIssue; manager: boolean; parentName?: string; nodes: TaxonomyNode[]; onResolved: () => void;
}) {
  // records at exactly the place the value is used (same level and parent): the only ones the server accepts
  const candidates = nodes.filter((n) => n.level === issue.level && (n.parent_id ?? null) === (issue.parent_id ?? null) && n.status === "ACTIVE");
  const suggested = issue.suggestions.map((s) => s.node_id);
  const [nodeId, setNodeId] = useState(issue.suggestions[0]?.node_id ?? "");
  const act = useAction();
  const level = LEVEL_LABEL[issue.level];
  const resolve = (action: "MAP" | "ALIAS" | "CREATE") => act.run(async () => {
    await resolveTaxonomy(job.job_id, { level: issue.level, value: issue.value, parent_id: issue.parent_id, action, ...(action === "CREATE" ? {} : { node_id: nodeId }) });
    onResolved();
  });
  return (
    <li>
      <p><strong>{level} “{issue.value}”</strong>{parentName && <span className="muted"> under “{parentName}”</span>}
        <span className="muted small"> · {issue.rows} row(s), e.g. row {issue.example_rows.join(", ")}</span></p>
      {issue.suggestions.length > 0 && <p className="small">Did you mean: {issue.suggestions.map((s) => `“${s.name}”`).join(", ")}?</p>}
      {manager ? (
        <div className="row wrap">
          <SelectField label={`Existing ${level.toLowerCase()}`} value={nodeId} onChange={(e) => setNodeId(e.target.value)}>
            <option value="">Choose…</option>
            {[...candidates].sort((a, b) => Number(suggested.includes(b.node_id)) - Number(suggested.includes(a.node_id)))
              .map((n) => <option key={n.node_id} value={n.node_id}>{n.name}{suggested.includes(n.node_id) ? " (suggested)" : ""}</option>)}
          </SelectField>
          <Button variant="secondary" small disabled={!nodeId} busy={act.busy} onClick={() => void resolve("MAP")}>Use for this import</Button>
          <Button variant="secondary" small disabled={!nodeId} busy={act.busy} onClick={() => void resolve("ALIAS")}>Always accept this name</Button>
          <Button variant="secondary" small busy={act.busy} onClick={() => void resolve("CREATE")}>Create “{issue.value}” as a new {level.toLowerCase()}</Button>
        </div>
      ) : <p className="small muted">Ask a provider administrator to resolve this name, or correct it in the rows.</p>}
      {act.error && <Alert kind="error">{act.error}</Alert>}
    </li>
  );
}
