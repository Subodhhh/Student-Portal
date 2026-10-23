import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import NeedBranch from "../components/NeedBranch";
import { Alert, Badge, Button, Card, ConfirmDialog, DataTable, EmptyState, Field, Modal, PageHeader, SelectField, Spinner, StatusBadge, TextAreaField } from "../components/ui";
import { useAction, useApi } from "../hooks/useApi";
import { API_URL, ApiError, api, uploadWithProgress } from "../services/api";
import { newRequestKey } from "../services/createTest";
import { shrinkPhoto, type DraftFile, type DraftQuestion, type PhotoDraft } from "../services/photos";

// Question Bank & Papers -> Photo papers. Photos of a printed paper (and, separately, of its answer key) are read by OCR on the server,
// shown as an editable preview next to the photos, and imported only after an explicit confirmation that passes the same validator as
// every other import. The import is a draft paper version that still goes through the normal approval.

type Subject = { subject_id: string; name: string };
/** Why the last import was refused. Kept by the page, not the preview: the preview is rebuilt when the refused attempt saved corrections. */
type Refusal = { message: string; questions: number[]; failed: { number: number; messages: string[] }[] };

export default function PhotoPapersPage() {
  return (
    <>
      <PageHeader crumbs={[<Link key="p" to="/papers?tab=imports">Question Bank & Papers</Link>, "Photo papers"]} title="Photo papers"
        subtitle="Photograph a printed question paper page by page (and its answer key), check what was read, correct it and import it." />
      <NeedBranch>{({ providerId, branchId }) => <Drafts key={branchId} providerId={providerId} branchId={branchId} />}</NeedBranch>
    </>
  );
}

function Drafts({ providerId, branchId }: { providerId: string; branchId: string }) {
  const navigate = useNavigate();
  const subjects = useApi<Subject[]>(`/api/v1/providers/${providerId}/subjects`);
  const list = useApi<{ items: { draft_id: string; title: string; subject_id: string; created_at: string; questions: number }[] }>(`/api/v1/photo-drafts?branch_id=${branchId}`);
  const [subjectId, setSubjectId] = useState("");
  const [title, setTitle] = useState("");
  const { busy, error, run } = useAction();
  const sName = (id: string) => subjects.data?.find((s) => s.subject_id === id)?.name ?? "—";
  return (
    <div className="stack">
      <Card>
        <h2>New photo paper</h2>
        {error && <Alert kind="error">{error}</Alert>}
        <div className="grid cols-2">
          <SelectField label="Subject" value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
            <option value="">Choose a subject</option>
            {(subjects.data ?? []).map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
          </SelectField>
          <Field label="Paper title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Physics weekly paper 4" />
        </div>
        <Button busy={busy} disabled={!subjectId || title.trim().length < 3} onClick={() => void run(async () => {
          const d = await api<PhotoDraft>(`/api/v1/branches/${branchId}/subjects/${subjectId}/photo-drafts`, { method: "POST", body: { title: title.trim() },
            headers: { "Idempotency-Key": newRequestKey() } });
          navigate(`/papers/photo/${d.draft_id}`);
        })}>Start</Button>
      </Card>
      <Card>
        <h2>Photo papers in progress</h2>
        {list.loading && !list.data ? <Spinner /> : (
          <DataTable rows={list.data?.items ?? []} rowKey={(d) => d.draft_id} empty={<EmptyState title="No photo papers in progress" />} columns={[
            { header: "Paper", render: (d) => <Link to={`/papers/photo/${d.draft_id}`}>{d.title}</Link> }, { header: "Subject", render: (d) => sName(d.subject_id) },
            { header: "Questions read", render: (d) => d.questions }, { header: "Started", render: (d) => new Date(d.created_at).toLocaleString() },
          ]} />
        )}
      </Card>
    </div>
  );
}

/** A stored photo, fetched with the session (it is served only to staff with access to the draft). */
function useImage(draftId: string, f: DraftFile | null) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!f) return undefined;
    let revoke: string | null = null;
    let cancelled = false;
    fetch(`${API_URL}/api/v1/photo-drafts/${draftId}/files/${f.draft_file_id}/content`, { credentials: "include" })
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => { if (b && !cancelled && b.type.startsWith("image/")) { revoke = URL.createObjectURL(b); setUrl(revoke); } })
      .catch(() => undefined);
    return () => { cancelled = true; if (revoke) URL.revokeObjectURL(revoke); };
  }, [draftId, f]);
  return url;
}

function Thumb({ draftId, f, onOpen }: { draftId: string; f: DraftFile; onOpen?: () => void }) {
  const url = useImage(draftId, f);
  return url ? <button type="button" className="photo-thumb" onClick={onOpen} aria-label={`View ${f.kind === "PAGE" ? "page" : "answer key"} ${f.position}`}>
    <img src={url} alt={`${f.kind === "PAGE" ? "Page" : "Answer key"} ${f.position}`} /></button>
    : <span className="muted small">{f.kind === "ANSWER_KEY" ? "key file" : "…"}</span>;
}

export function PhotoDraftPage() {
  const { draftId = "" } = useParams();
  const navigate = useNavigate();
  const d = useApi<PhotoDraft>(`/api/v1/photo-drafts/${draftId}`);
  const [viewing, setViewing] = useState<DraftFile | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const discard = useAction();
  if (d.loading && !d.data) return <Spinner />;
  if (d.error || !d.data) return <Alert kind="error">{d.error || "Photo paper not found."}</Alert>;
  const draft = d.data;
  const pages = draft.files.filter((f) => f.kind === "PAGE");
  const keys = draft.files.filter((f) => f.kind === "ANSWER_KEY");
  const open = draft.status === "DRAFT";
  return (
    <div className="stack">
      <PageHeader crumbs={[<Link key="p" to="/papers?tab=imports">Question Bank & Papers</Link>, <Link key="ph" to="/papers/photo">Photo papers</Link>, draft.title]}
        title={draft.title} actions={<span className="row"><StatusBadge status={draft.status} />
          {open && <Button small variant="danger" onClick={() => setDiscarding(true)}>Discard</Button>}</span>} />
      {draft.status === "ACCEPTED" && draft.accepted_paper_version_id && (
        <Alert kind="success">Imported as a draft paper version. Review, approve and publish it as usual: <Link to={`/papers/${draft.accepted_paper_version_id}`}>open the paper</Link>.</Alert>)}
      <FilesCard title="1. Question paper photos" kind="PAGE" draft={draft} files={pages} open={open} onChange={d.reload} onView={setViewing}
        hint="Photograph each page flat, in good light, in page order. Up to 20 pages; photos are shrunk on your phone before upload." />
      <FilesCard title="2. Answer key (separate)" kind="ANSWER_KEY" draft={draft} files={keys} open={open} onChange={d.reload} onView={setViewing}
        hint="Photos of the answer key, or a .csv/.txt file with 'number,answer' lines (1,B). Answers are matched to questions by number." />
      {/* remounted whenever the server's preview changes (new extraction, saved corrections, acceptance) */}
      <Preview key={`${draft.extracted_at}-${draft.corrections}-${draft.status}`} draft={draft} pages={pages} open={open} onChange={d.reload} onView={setViewing}
        refusal={refusal} onRefusal={setRefusal} />
      {discarding && (
        <ConfirmDialog title={`Discard “${draft.title}”?`} danger confirmLabel="Discard" busy={discard.busy} onCancel={() => setDiscarding(false)}
          message="The photos and corrections are kept as history, but nothing is imported and the draft can no longer be changed."
          onConfirm={() => void discard.run(async () => {
            await api(`/api/v1/photo-drafts/${draft.draft_id}/discard`, { method: "POST" });
            navigate("/papers/photo");
          })}>
          {discard.error && <Alert kind="error">{discard.error}</Alert>}
        </ConfirmDialog>
      )}
      {viewing && <Modal title={`${viewing.kind === "PAGE" ? "Page" : "Answer key"} ${viewing.position}`} onClose={() => setViewing(null)}><FullImage draftId={draft.draft_id} f={viewing} /></Modal>}
    </div>
  );
}

function FullImage({ draftId, f }: { draftId: string; f: DraftFile }) {
  const url = useImage(draftId, f);
  return url ? <img src={url} alt="" style={{ maxWidth: "100%" }} /> : <Spinner />;
}

function FilesCard({ title, kind, draft, files, open, onChange, onView, hint }: {
  title: string; kind: "PAGE" | "ANSWER_KEY"; draft: PhotoDraft; files: DraftFile[]; open: boolean; onChange: () => void; onView: (f: DraftFile) => void; hint: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<{ done: number; total: number; fraction: number } | null>(null);
  const act = useAction();
  const add = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    const chosen = [...list];
    void act.run(async () => {
      let position = files.length;
      for (let i = 0; i < chosen.length; i++) {
        position += 1;
        const blob = chosen[i].name.toLowerCase().match(/\.(csv|txt)$/) ? chosen[i] : await shrinkPhoto(chosen[i]);
        const form = new FormData();
        form.append("kind", kind);
        form.append("position", String(position));
        form.append("file", blob, blob === chosen[i] ? chosen[i].name : chosen[i].name.replace(/\.[^.]+$/, "") + ".jpg");
        setProgress({ done: i, total: chosen.length, fraction: 0 });
        await uploadWithProgress(`/api/v1/photo-drafts/${draft.draft_id}/files`, form, (fraction) => setProgress({ done: i, total: chosen.length, fraction }));
      }
      setProgress(null);
      if (input.current) input.current.value = "";
      onChange();
    }).finally(() => setProgress(null));
  };
  const move = (f: DraftFile, delta: number) => void act.run(async () => {
    const ids = files.map((x) => x.draft_file_id);
    const i = ids.indexOf(f.draft_file_id);
    [ids[i], ids[i + delta]] = [ids[i + delta], ids[i]];
    await api(`/api/v1/photo-drafts/${draft.draft_id}/files/order`, { method: "PUT", body: { kind, draft_file_ids: ids } });
    onChange();
  });
  const remove = (f: DraftFile) => void act.run(async () => {
    await api(`/api/v1/photo-drafts/${draft.draft_id}/files/${f.draft_file_id}`, { method: "DELETE" });
    onChange();
  });
  // OCR the stored photo again: automatically, or turned upright by the uploader (the photo itself is not changed).
  const readAgain = (f: DraftFile, rotate: number | null) => void act.run(async () => {
    await api(`/api/v1/photo-drafts/${draft.draft_id}/files/${f.draft_file_id}/read-again`, { method: "POST", body: rotate === null ? {} : { rotate } });
    onChange();
  });
  return (
    <Card>
      <h2>{title}</h2>
      <p className="muted small">{hint}</p>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {progress && <p role="status">Uploading and reading {progress.done + 1} of {progress.total} ({Math.round(progress.fraction * 100)}%)…
        <progress value={progress.done + progress.fraction} max={progress.total} /></p>}
      {files.length > 0 && (
        <ol className="photo-list">{files.map((f, i) => (
          <li key={f.draft_file_id}>
            <Thumb draftId={draft.draft_id} f={f} onOpen={() => onView(f)} />
            <span className="small">{kind === "PAGE" ? "Page" : "Key"} {f.position} · {f.ocr_error ? <Badge tone="danger">{f.ocr_error}</Badge> : `${f.lines} line(s) read`}</span>
            {open && <span className="row">
              <Button small variant="secondary" disabled={i === 0 || act.busy} onClick={() => move(f, -1)} aria-label={`Move ${f.position} up`}>↑</Button>
              <Button small variant="secondary" disabled={i === files.length - 1 || act.busy} onClick={() => move(f, 1)} aria-label={`Move ${f.position} down`}>↓</Button>
              <Button small variant="secondary" disabled={act.busy} onClick={() => readAgain(f, null)} aria-label={`Read ${f.position} again`}>Read again</Button>
              <select className="small" disabled={act.busy} value="" aria-label={`Turn ${f.position} upright and read again`}
                onChange={(e) => e.target.value && readAgain(f, Number(e.target.value))}>
                <option value="">Turn…</option><option value="90">Turn right ↻</option><option value="180">Upside down</option><option value="270">Turn left ↺</option>
              </select>
              <Button small variant="danger" disabled={act.busy} onClick={() => remove(f)} aria-label={`Remove ${f.position}`}>Remove</Button>
            </span>}
          </li>))}
        </ol>
      )}
      {open && (
        <label className="btn secondary">
          {kind === "PAGE" ? "Add page photos" : "Add answer-key photos or file"}
          <input ref={input} type="file" multiple hidden capture="environment" aria-label={kind === "PAGE" ? "Add page photos" : "Add answer-key photos or file"}
            accept={kind === "PAGE" ? "image/jpeg,image/png,image/webp" : "image/jpeg,image/png,image/webp,.csv,.txt,text/csv,text/plain"} onChange={(e) => add(e.target.files)} />
        </label>
      )}
    </Card>
  );
}

const editable = (q: DraftQuestion) => ({ number: q.number, question: q.question, option_a: q.option_a, option_b: q.option_b, option_c: q.option_c, option_d: q.option_d,
  correct: q.correct, marks: q.marks, negative_marks: q.negative_marks, explanation: q.explanation, topic: q.topic, reviewed: q.reviewed, has_diagram: q.has_diagram });

function Preview({ draft, pages, open, onChange, onView, refusal, onRefusal }: {
  draft: PhotoDraft; pages: DraftFile[]; open: boolean; onChange: () => void; onView: (f: DraftFile) => void; refusal: Refusal | null; onRefusal: (r: Refusal | null) => void;
}) {
  const [edits, setEdits] = useState<DraftQuestion[]>(draft.questions);
  const [dirty, setDirty] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [defaults, setDefaults] = useState({ marks: "", negative: "" });
  const missingMarks = edits.filter((q) => !q.marks.trim() || !q.negative_marks.trim()).length;
  const applyDefaults = () => {        // printed papers rarely show marks per question: the uploader states them once, nothing is assumed
    setEdits((qs) => qs.map((q) => ({ ...q, marks: q.marks.trim() || defaults.marks.trim(), negative_marks: q.negative_marks.trim() || defaults.negative.trim() })));
    setDirty(true);
  };
  const act = useAction();
  const set = (i: number, patch: Partial<DraftQuestion>) => { setEdits((qs) => qs.map((q, j) => (j === i ? { ...q, ...patch } : q))); setDirty(true); };
  const moveQ = (i: number, delta: number) => { setEdits((qs) => { const n = [...qs]; [n[i], n[i + delta]] = [n[i + delta], n[i]]; return n; }); setDirty(true); };
  const save = () => api<PhotoDraft>(`/api/v1/photo-drafts/${draft.draft_id}/questions`, { method: "PUT", body: { questions: edits.map(editable) } });
  const extract = (keyOnly: boolean) => void act.run(async () => {
    if (dirty && keyOnly) await save();
    await api(`/api/v1/photo-drafts/${draft.draft_id}/extract${keyOnly ? "?answer_key_only=true" : ""}`, { method: "POST" });
    onRefusal(null); onChange();
  });
  const accept = () => void act.run(async () => {
    onRefusal(null);
    if (dirty) await save();
    try {
      await api(`/api/v1/photo-drafts/${draft.draft_id}/accept`, { method: "POST", body: { confirm: true } });
      setConfirm(false); onChange();
    } catch (e) {
      setConfirm(false);
      if (!(e instanceof ApiError) || e.status !== 422) throw e;
      const byQ = new Map<number, string[]>();
      for (const er of (Array.isArray(e.extra.errors) ? e.extra.errors : []) as { row: number | null; message: string }[]) {
        byQ.set(er.row ?? 0, [...(byQ.get(er.row ?? 0) ?? []), er.message]);
      }
      onRefusal({ message: e.message, questions: Array.isArray(e.extra.questions) ? (e.extra.questions as number[]) : [],
        failed: [...byQ.entries()].map(([number, messages]) => ({ number, messages })) });
      onChange();                                       // the corrections saved above are kept; the preview is rebuilt from them
    }
  });
  const unchecked = edits.filter((q) => q.needs_review && !q.reviewed).length;
  return (
    <Card>
      <div className="card-head"><h2>3. Check and correct</h2>
        {open && <div className="row">
          <Button variant="secondary" busy={act.busy} disabled={pages.length === 0} onClick={() => extract(false)}>{draft.extracted_at ? "Read the photos again" : "Read questions from the photos"}</Button>
          {draft.extracted_at && <Button variant="secondary" busy={act.busy} onClick={() => extract(true)}>Re-match the answer key</Button>}
        </div>}
      </div>
      <p className="muted small">OCR is not perfect. Highlighted questions were read with low confidence or have a problem: compare them with the photo, correct them and tick
        “I checked this question”. Nothing is imported until you confirm, and then only if every question is complete.</p>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {draft.issues.map((i, k) => <Alert key={k} kind="warn">{i}</Alert>)}
      {refusal && (
        <Alert kind="error">
          <strong>Nothing was imported.</strong> {refusal.message.replace(/^Nothing was imported:\s*/, "")}
          {refusal.failed.length === 0 && refusal.questions.length > 0 && <> Questions: {refusal.questions.join(", ")}.</>}
          {refusal.failed.length > 0 && <ul className="plain">{refusal.failed.map((f) => <li key={f.number}>{f.number ? `Question ${f.number}: ` : ""}{f.messages.join(" ")}</li>)}</ul>}
        </Alert>
      )}
      {!draft.extracted_at ? <EmptyState title="Nothing read yet" hint="Add the page photos, then read the questions." /> : (
        <div className="stack">
          {open && missingMarks > 0 && (
            <div className="row wrap" role="group" aria-label="Marks for questions without marks">
              <span className="small">{missingMarks} question(s) have no marks on the photo. Marks for each of them:</span>
              <Field label="Marks for a correct answer" type="number" min={0} value={defaults.marks} onChange={(e) => setDefaults({ ...defaults, marks: e.target.value })} />
              <Field label="Negative marks for a wrong answer" type="number" min={0} value={defaults.negative} onChange={(e) => setDefaults({ ...defaults, negative: e.target.value })} />
              <Button variant="secondary" disabled={!defaults.marks.trim() || !defaults.negative.trim()} onClick={applyDefaults}>Apply to questions without marks</Button>
            </div>
          )}
          {edits.map((q, i) => {
            const page = pages.find((p) => p.position === q.page);
            return (
              <section key={q.number} className={`photo-question${q.needs_review && !q.reviewed ? " needs-review" : ""}`} aria-label={`Question ${q.number}`}>
                <div className="row between">
                  <strong>Question {q.number}</strong>
                  <span className="row">
                    <span title="Tesseract's score for the least clear line of this question. A heuristic, not a probability."><Badge tone={q.confidence >= 0.98 ? "ok" : q.confidence >= 0.8 ? "warn" : "danger"}>OCR score {Math.round(q.confidence * 100)}/100</Badge></span>
                    {page && <Button small variant="secondary" onClick={() => onView(page)}>Photo of page {page.position}</Button>}
                    {open && <><Button small variant="secondary" disabled={i === 0} onClick={() => moveQ(i, -1)} aria-label={`Move question ${q.number} up`}>↑</Button>
                      <Button small variant="secondary" disabled={i === edits.length - 1} onClick={() => moveQ(i, 1)} aria-label={`Move question ${q.number} down`}>↓</Button></>}
                  </span>
                </div>
                {q.issues.map((m, k) => <Alert key={k} kind="warn">{m}</Alert>)}
                {q.needs_review && !!q.source?.length && (
                  <details className="small">
                    <summary>What was read from the photo</summary>
                    <ul className="ocr-source">{q.source.map((s, k) => <li key={k}><span className="muted">p{s.page} · {s.confidence}</span> {s.text}</li>)}
                      {q.answer_source && <li><span className="muted">answer key</span> {q.answer_source}</li>}</ul>
                  </details>
                )}
                <TextAreaField label={`Question ${q.number} text`} value={q.question} disabled={!open} onChange={(e) => set(i, { question: e.target.value })} />
                <div className="grid cols-2">
                  {(["a", "b", "c", "d"] as const).map((o) => (
                    <Field key={o} label={`Question ${q.number} option ${o.toUpperCase()}`} value={q[`option_${o}`]} disabled={!open}
                      onChange={(e) => set(i, { [`option_${o}`]: e.target.value } as Partial<DraftQuestion>)} />
                  ))}
                </div>
                <div className="grid cols-3">
                  <SelectField label={`Question ${q.number} correct answer`} value={q.correct} disabled={!open} onChange={(e) => set(i, { correct: e.target.value })}>
                    <option value="">Not set</option>{["A", "B", "C", "D"].map((x) => <option key={x}>{x}</option>)}
                  </SelectField>
                  <Field label={`Question ${q.number} marks`} value={q.marks} disabled={!open} onChange={(e) => set(i, { marks: e.target.value })} />
                  <Field label={`Question ${q.number} negative marks`} value={q.negative_marks} disabled={!open} onChange={(e) => set(i, { negative_marks: e.target.value })} />
                </div>
                {open && <div className="row">
                  <label className="row checkbox-field"><input type="checkbox" checked={q.reviewed} onChange={(e) => set(i, { reviewed: e.target.checked })} /> I checked question {q.number} against the photo</label>
                  <label className="row checkbox-field"><input type="checkbox" checked={q.has_diagram} onChange={(e) => set(i, { has_diagram: e.target.checked })} /> Question {q.number} needs its diagram</label>
                </div>}
              </section>
            );
          })}
          {open && <div className="row">
            <Button variant="secondary" busy={act.busy} disabled={!dirty} onClick={() => void act.run(async () => { await save(); onRefusal(null); onChange(); })}>Save corrections</Button>
            <Button busy={act.busy} disabled={edits.length === 0} onClick={() => setConfirm(true)}>Import paper</Button>
            {unchecked > 0 && <span className="muted small">{unchecked} highlighted question(s) still to check.</span>}
          </div>}
        </div>
      )}
      {confirm && (
        <ConfirmDialog title="Import this paper?" confirmLabel="Import" busy={act.busy} onCancel={() => setConfirm(false)} onConfirm={accept}
          message={`I checked the ${edits.length} question(s) against the photos. The paper is imported as a draft version for the usual approval; nothing is published.`} />
      )}
    </Card>
  );
}
