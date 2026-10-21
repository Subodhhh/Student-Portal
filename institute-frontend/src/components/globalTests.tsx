import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isGlobalRole } from "../auth/roles";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "./ui";

export type GlobalSection = { position: number; subject_id: string; subject: string | null; paper_id: string; paper_title: string; paper_version_id: string;
  version_no: number; version_status: string; question_count: number; total_marks: string };
export type GlobalTest = { test_id: string; name: string; description: string | null; exam_type: string | null; target_class: string | null; duration_minutes: number;
  status: string; derived_from_test_id: string | null; created_at: string; published_at: string | null; publisher: string | null; question_count: number;
  total_marks: string; sections: GlobalSection[] };
type PaperRow = { paper_id: string; subject_id: string; title: string; latest_version: { paper_version_id: string; version_no: number; status: string; question_count: number; total_marks: string } | null };
type Subject = { subject_id: string; name: string };

const subjectsOf = (t: GlobalTest) => t.sections.map((s) => s.subject ?? "Subject").join(", ");

/** Global Tests: platform administrators create, publish, withdraw and derive them; providers see published tests, schedule
 * them for their own students and (with `onContribute`) offer papers for them. The published test itself never changes. */
export function GlobalTestsPanel({ onContribute }: { onContribute?: (t: GlobalTest) => void } = {}) {
  const { me, can } = useAuth();
  const platform = isGlobalRole(me?.primary_role) && can("examplan.manage");
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<GlobalTest | "new" | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "publish" | "withdraw"; test: GlobalTest } | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const act = useAction();
  const list = useApi<Page<GlobalTest>>(`/api/v1/global-tests?page=${page}&page_size=20${status ? `&status=${status}` : ""}`);
  const done = (msg: string) => { setNotice(msg); setConfirm(null); setReason(""); list.reload(); };
  return (
    <div className="stack">
      <p className="muted">
        {platform ? "Tests published by the platform. Providers schedule a published test for their own students; once published, a test never changes (create a new version to change it)."
          : "Published by the platform. Scheduling one assigns it to your students exactly as published: its papers, duration and marking cannot be changed."}
      </p>
      <div className="toolbar">
        {platform && (
          <SelectField label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All</option><option value="DRAFT">Draft</option><option value="PUBLISHED">Published</option><option value="WITHDRAWN">Withdrawn</option>
          </SelectField>
        )}
        {platform && <Button onClick={() => { setNotice(""); setEditing("new"); }}>New Global Test</Button>}
      </div>
      {notice && <Alert kind="success">{notice}</Alert>}
      {act.error && !confirm && <Alert kind="error">{act.error}</Alert>}
      {list.error && <Alert kind="error">{list.error} <Button small variant="secondary" onClick={list.reload}>Retry</Button></Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <>
          <DataTable rows={list.data?.items ?? []} rowKey={(t) => t.test_id}
            empty={<EmptyState title={platform ? "No Global Tests yet" : "No published Global Tests"} hint={platform ? "Build platform papers in Question Bank & Papers, then create a test from them." : undefined} />}
            columns={[
              { header: "Test", render: (t) => <><Link to={`/tests/global/${t.test_id}`}><strong>{t.name}</strong></Link>{t.description && <div className="muted small">{t.description}</div>}</> },
              { header: "Exam / class", render: (t) => [t.exam_type, t.target_class].filter(Boolean).join(" · ") || "—" },
              { header: "Subjects", render: subjectsOf },
              { header: "Questions", render: (t) => t.question_count },
              { header: "Marks", render: (t) => Number(t.total_marks) },
              { header: "Duration", render: (t) => `${t.duration_minutes} min` },
              { header: "Status", render: (t) => <><StatusBadge status={t.status} />{t.published_at && <div className="muted small">{new Date(t.published_at).toLocaleDateString()}{t.publisher ? ` · ${t.publisher}` : ""}</div>}</> },
              { header: "Actions", render: (t) => (
                <div className="row">
                  {t.status === "PUBLISHED" && can("examplan.manage") && <Link className="btn small" to={`/tests/global/${t.test_id}/schedule`}>Schedule</Link>}
                  {onContribute && t.status === "PUBLISHED" && <Button small variant="secondary" onClick={() => onContribute(t)}>Offer a paper</Button>}
                  {platform && t.status === "DRAFT" && <Button small variant="secondary" onClick={() => setEditing(t)}>Edit</Button>}
                  {platform && t.status === "DRAFT" && <Button small onClick={() => setConfirm({ kind: "publish", test: t })}>Publish</Button>}
                  {platform && t.status === "PUBLISHED" && <Button small variant="danger" onClick={() => setConfirm({ kind: "withdraw", test: t })}>Withdraw</Button>}
                  {platform && t.status !== "DRAFT" && <Button small variant="secondary" busy={act.busy} onClick={() => void act.run(async () => {
                    const copy = await api<GlobalTest>(`/api/v1/global-tests/${t.test_id}/derive`, { method: "POST" });
                    done(`Created the draft “${copy.name}”. The original is unchanged.`);
                  })}>Create new version</Button>}
                </div>
              ) },
            ]} />
          <Pagination page={page} pageSize={20} total={list.data?.total ?? 0} onChange={setPage} />
        </>
      )}
      {editing && <GlobalTestForm test={editing === "new" ? null : editing} onClose={() => setEditing(null)} onDone={(t) => { setEditing(null); done(`Saved the draft “${t.name}”.`); }} />}
      {confirm && (
        <ConfirmDialog title={`${confirm.kind === "publish" ? "Publish" : "Withdraw"} “${confirm.test.name}”`} danger={confirm.kind === "withdraw"}
          confirmLabel={confirm.kind === "publish" ? "Publish" : "Withdraw"} busy={act.busy} onCancel={() => { setConfirm(null); setReason(""); }}
          message={confirm.kind === "publish"
            ? "Providers can then schedule it. After publishing, its papers, duration and marking can never change."
            : "Providers can no longer schedule it and plans that schedule it stop generating. Exams already generated, attempts and results are unchanged."}
          onConfirm={() => void act.run(async () => {
            await api(`/api/v1/global-tests/${confirm.test.test_id}/${confirm.kind}`, { method: "POST", body: confirm.kind === "withdraw" ? { reason } : undefined });
            done(confirm.kind === "publish" ? `“${confirm.test.name}” is published.` : `“${confirm.test.name}” was withdrawn.`);
          })}>
          {act.error && <Alert kind="error">{act.error}</Alert>}
          {confirm.kind === "withdraw" && <TextAreaField label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />}
        </ConfirmDialog>
      )}
    </div>
  );
}

export function GlobalTestForm({ test, onClose, onDone }: { test: GlobalTest | null; onClose: () => void; onDone: (t: GlobalTest) => void }) {
  const papers = useApi<Page<PaperRow>>("/api/v1/papers?scope=GLOBAL&page_size=100");
  const subjects = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  // Platform copies of approved provider contributions, labelled so the administrator knows where a paper came from.
  const contributed = useApi<Page<{ global_paper_id: string | null; provider_name: string | null; test_name: string }>>("/api/v1/global-test-contributions?status=APPROVED&page_size=100");
  const origin = (paperId: string) => contributed.data?.items.find((c) => c.global_paper_id === paperId);
  const [f, setF] = useState({ name: test?.name ?? "", description: test?.description ?? "", exam_type: test?.exam_type ?? "", target_class: test?.target_class ?? "",
    duration: String(test?.duration_minutes ?? 60) });
  const [picked, setPicked] = useState<string[]>(test?.sections.map((s) => s.paper_version_id) ?? []);
  const { busy, error, run } = useAction();
  const usable = (papers.data?.items ?? []).filter((p) => p.latest_version && ["APPROVED", "PUBLISHED"].includes(p.latest_version.status));
  const sName = (id: string) => subjects.data?.items.find((s) => s.subject_id === id)?.name ?? "Subject";
  const toggle = (vid: string) => setPicked((c) => (c.includes(vid) ? c.filter((x) => x !== vid) : [...c, vid]));
  const ready = f.name.trim().length >= 3 && Number(f.duration) > 0 && Number(f.duration) <= 720 && picked.length > 0;
  return (
    <Modal title={test ? `Edit draft: ${test.name}` : "New Global Test"} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <TextAreaField label="Description (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="grid cols-3">
        <Field label="Exam type" value={f.exam_type} onChange={(e) => setF({ ...f, exam_type: e.target.value })} placeholder="NEET" />
        <Field label="Class" value={f.target_class} onChange={(e) => setF({ ...f, target_class: e.target.value })} placeholder="Class 12" />
        <Field label="Duration (minutes)" type="number" min={1} max={720} value={f.duration} onChange={(e) => setF({ ...f, duration: e.target.value })} />
      </div>
      <h3>Sections: approved platform papers (one per subject)</h3>
      <p className="muted small">Approved provider contributions appear here once their platform copy has passed the normal paper approval. Choose one paper per subject.</p>
      {papers.loading && !papers.data ? <Spinner /> : usable.length === 0 ? (
        <EmptyState title="No approved platform papers" hint="Build platform papers in Question Bank & Papers and approve them first." />
      ) : (
        <fieldset className="checklist">
          <legend className="sr-only">Platform papers</legend>
          {usable.map((p) => (
            <label key={p.paper_id} className="row">
              <input type="checkbox" checked={picked.includes(p.latest_version!.paper_version_id)} onChange={() => toggle(p.latest_version!.paper_version_id)} />
              {sName(p.subject_id)}: {p.title} v{p.latest_version!.version_no} · {p.latest_version!.question_count} questions · {Number(p.latest_version!.total_marks)} marks
              <StatusBadge status={p.latest_version!.status} />
              {origin(p.paper_id) && <span className="muted small">contributed by {origin(p.paper_id)!.provider_name} for {origin(p.paper_id)!.test_name}</span>}
            </label>
          ))}
        </fieldset>
      )}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!ready} onClick={() => void run(async () => {
          const body = { name: f.name.trim(), description: f.description.trim() || null, exam_type: f.exam_type.trim() || null, target_class: f.target_class.trim() || null,
            duration_minutes: Number(f.duration), sections: picked.map((paper_version_id) => ({ paper_version_id })) };
          onDone(await api<GlobalTest>(test ? `/api/v1/global-tests/${test.test_id}` : "/api/v1/global-tests", { method: test ? "PUT" : "POST", body }));
        })}>Save draft</Button>
      </div>
    </Modal>
  );
}
