import { useState } from "react";
import { ClassTargetSelect } from "./classLevels";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isGlobalRole } from "../auth/roles";
import { useAction, useApi } from "../hooks/useApi";
import { api, type Page } from "../services/api";
import { subjectsOf } from "../services/globalSchedule";
import { Alert, Button, ConfirmDialog, DataTable, EmptyState, Field, Modal, Pagination, SelectField, Spinner, StatusBadge, TextAreaField } from "./ui";

export type GlobalSection = { position: number; subject_id: string; subject: string | null; paper_id: string; paper_title: string; paper_version_id: string;
  version_no: number; version_status: string; question_count: number; total_marks: string };
export type GlobalTest = { test_id: string; name: string; description: string | null; exam_type: string | null; target_class: string | null; duration_minutes: number;
  status: string; derived_from_test_id: string | null; created_at: string; published_at: string | null; publisher: string | null; question_count: number;
  total_marks: string; sections: GlobalSection[];
  // SCHEDULE: subjects + central dates, providers submit papers. SECTIONS: earlier tests built from platform papers.
  kind: "SCHEDULE" | "SECTIONS"; subjects: { subject_id: string; subject: string | null }[]; marking_mode: string; marks_correct: number | null;
  marks_wrong: number | null; generate_lead_minutes: number; occurrences: Record<string, number>; next_occurrence_at: string | null };
type PaperRow = { paper_id: string; subject_id: string; title: string; latest_version: { paper_version_id: string; version_no: number; status: string; question_count: number; total_marks: string } | null };
type Subject = { subject_id: string; name: string };


/** Global Tests (definitions): platform administrators create them (subjects, duration, marking - no paper needed), schedule their
 * dates and publish them on the test's page; providers see the published tests that concern them. Earlier tests built from platform
 * papers (kind SECTIONS) keep their own actions. A published test never changes. */
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
        {platform ? "Create a Global Test with its subjects, duration and marking, then plan and publish its dates on the test's page. Providers submit their own papers for each date."
          : "Tests the platform schedules. Open one to see its dates and submit your papers for your students."}
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
            empty={<EmptyState title={platform ? "No Global Tests yet" : "No published Global Tests"} hint={platform ? "Create one: no paper is needed, providers submit theirs for each date." : undefined} />}
            columns={[
              { header: "Test", render: (t) => <><Link to={`/tests/global/${t.test_id}`}><strong>{t.name}</strong></Link>{t.description && <div className="muted small">{t.description}</div>}</> },
              { header: "Exam / class", render: (t) => [t.exam_type, t.target_class].filter(Boolean).join(" · ") || "—" },
              { header: "Subjects", render: subjectsOf },
              { header: "Dates", render: (t) => (t.kind === "SCHEDULE"
                ? <>{t.occurrences.PUBLISHED ?? 0} published{platform && t.occurrences.DRAFT ? ` · ${t.occurrences.DRAFT} draft` : ""}
                  {t.next_occurrence_at && <div className="muted small">next {new Date(t.next_occurrence_at).toLocaleString()}</div>}</>
                : <span className="muted small">{t.question_count} questions · {Number(t.total_marks)} marks (platform papers)</span>) },
              { header: "Duration", render: (t) => `${t.duration_minutes} min` },
              { header: "Status", render: (t) => <><StatusBadge status={t.status} />{t.published_at && <div className="muted small">{new Date(t.published_at).toLocaleDateString()}{t.publisher ? ` · ${t.publisher}` : ""}</div>}</> },
              { header: "Actions", render: (t) => (
                <div className="row">
                  <Link className="btn small secondary" to={`/tests/global/${t.test_id}`}>{t.kind === "SCHEDULE" ? "Open schedule" : "Open"}</Link>
                  {t.kind === "SECTIONS" && t.status === "PUBLISHED" && can("examplan.manage") && <Link className="btn small" to={`/tests/global/${t.test_id}/schedule`}>Schedule</Link>}
                  {onContribute && t.kind === "SECTIONS" && t.status === "PUBLISHED" && <Button small variant="secondary" onClick={() => onContribute(t)}>Offer a paper</Button>}
                  {platform && t.status === "DRAFT" && <Button small variant="secondary" onClick={() => setEditing(t)}>Edit</Button>}
                  {platform && t.kind === "SECTIONS" && t.status === "DRAFT" && <Button small onClick={() => setConfirm({ kind: "publish", test: t })}>Publish</Button>}
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
      {editing && (editing !== "new" && editing.kind === "SECTIONS"
        ? <SectionsTestForm test={editing} onClose={() => setEditing(null)} onDone={(t) => { setEditing(null); done(`Saved the draft “${t.name}”.`); }} />
        : <GlobalTestForm test={editing === "new" ? null : editing} onClose={() => setEditing(null)}
          onDone={(t) => { setEditing(null); done(`Saved the draft “${t.name}”. Open it to plan and publish its dates.`); }} />)}
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

/** A Global Test definition: subjects, duration and marking. No paper is chosen here: providers submit theirs for each date. */
export function GlobalTestForm({ test, onClose, onDone }: { test: GlobalTest | null; onClose: () => void; onDone: (t: GlobalTest) => void }) {
  const subjects = useApi<Page<Subject & { status?: string }>>("/api/v1/subjects?page_size=200");
  const [f, setF] = useState({ name: test?.name ?? "", description: test?.description ?? "", exam_type: test?.exam_type ?? "", target_class: test?.target_class ?? "",
    duration: String(test?.duration_minutes ?? 60), marking: test?.marking_mode ?? "UNIFORM", correct: String(test?.marks_correct ?? 4),
    wrong: String(test?.marks_wrong ?? 1), leadHours: String((test?.generate_lead_minutes ?? 120) / 60) });
  const [picked, setPicked] = useState<string[]>(test?.subjects.map((s) => s.subject_id) ?? []);
  const { busy, error, run } = useAction();
  const uniform = f.marking === "UNIFORM";
  const active = (subjects.data?.items ?? []).filter((s) => !s.status || s.status === "ACTIVE");
  const toggle = (id: string) => setPicked((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  const lead = Math.round(Number(f.leadHours) * 60);
  const incomplete = f.name.trim().length < 3 ? "Enter a name (at least 3 characters)." : picked.length === 0 ? "Choose at least one subject."
    : !(Number(f.duration) > 0 && Number(f.duration) <= 720) ? "Duration must be 1 to 720 minutes."
      : uniform && !(Number(f.correct) >= 1 && Number(f.wrong) >= 0) ? "Enter the marks for correct and wrong answers."
        : !(lead >= 0 && lead <= 10080) ? "The paper deadline must be 0 to 168 hours before the test." : null;
  return (
    <Modal title={test ? `Edit draft: ${test.name}` : "New Global Test"} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <TextAreaField label="Description (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="grid cols-3">
        <Field label="Exam type" value={f.exam_type} onChange={(e) => setF({ ...f, exam_type: e.target.value })} placeholder="NEET" />
        <ClassTargetSelect label="Class" value={f.target_class} onChange={(v) => setF({ ...f, target_class: v })} />
        <Field label="Duration (minutes)" type="number" min={1} max={720} value={f.duration} onChange={(e) => setF({ ...f, duration: e.target.value })} />
      </div>
      <fieldset className="checklist"><legend>Subjects (each provider submits its own paper per subject)</legend>
        {subjects.loading && !subjects.data ? <Spinner /> : active.length === 0 ? <EmptyState title="No subjects" hint="Create subjects first." /> : (
          <div className="check-grid">{active.map((s) => (
            <label key={s.subject_id}><input type="checkbox" checked={picked.includes(s.subject_id)} onChange={() => toggle(s.subject_id)} /> {s.name}</label>
          ))}</div>
        )}
      </fieldset>
      <div className="grid cols-3">
        <SelectField label="Marking" value={f.marking} onChange={(e) => setF({ ...f, marking: e.target.value })}>
          <option value="UNIFORM">Same marks for every question</option><option value="PER_QUESTION">Marks from each paper</option>
        </SelectField>
        {uniform && <Field label="Marks for a correct answer" type="number" min={1} max={100} value={f.correct} onChange={(e) => setF({ ...f, correct: e.target.value })} />}
        {uniform && <Field label="Marks deducted for a wrong answer" type="number" min={0} max={100} value={f.wrong} onChange={(e) => setF({ ...f, wrong: e.target.value })} />}
      </div>
      <Field label="Paper deadline (hours before each test opens)" type="number" min={0} max={168} step="0.5" value={f.leadHours}
        onChange={(e) => setF({ ...f, leadHours: e.target.value })} hint="Providers submit papers until then; the exams are generated at that moment and never change afterwards." />
      <div className="modal-actions">
        {incomplete && <span className="muted small">{incomplete}</span>}
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={busy} disabled={!!incomplete} onClick={() => void run(async () => {
          const body = { name: f.name.trim(), description: f.description.trim() || null, exam_type: f.exam_type.trim() || null, target_class: f.target_class.trim() || null,
            duration_minutes: Number(f.duration), subject_ids: picked, marking_mode: f.marking, marks_correct: uniform ? Number(f.correct) : null,
            marks_wrong: uniform ? Number(f.wrong) : null, generate_lead_minutes: lead };
          onDone(await api<GlobalTest>(test ? `/api/v1/global-tests/${test.test_id}` : "/api/v1/global-tests", { method: test ? "PUT" : "POST", body }));
        })}>Save draft</Button>
      </div>
    </Modal>
  );
}

/** Editing an earlier draft built from platform papers (kind SECTIONS). */
function SectionsTestForm({ test, onClose, onDone }: { test: GlobalTest; onClose: () => void; onDone: (t: GlobalTest) => void }) {
  const papers = useApi<Page<PaperRow>>("/api/v1/papers?scope=GLOBAL&page_size=100");
  const subjects = useApi<Page<Subject>>("/api/v1/subjects?page_size=100");
  // Platform copies of approved provider contributions, labelled so the administrator knows where a paper came from.
  const contributed = useApi<Page<{ global_paper_id: string | null; provider_name: string | null; test_name: string }>>("/api/v1/global-test-contributions?status=APPROVED&page_size=100");
  const origin = (paperId: string) => contributed.data?.items.find((c) => c.global_paper_id === paperId);
  const [f, setF] = useState({ name: test.name, description: test.description ?? "", exam_type: test.exam_type ?? "", target_class: test.target_class ?? "",
    duration: String(test.duration_minutes) });
  const [picked, setPicked] = useState<string[]>(test.sections.map((s) => s.paper_version_id));
  const { busy, error, run } = useAction();
  const usable = (papers.data?.items ?? []).filter((p) => p.latest_version && ["APPROVED", "PUBLISHED"].includes(p.latest_version.status));
  const sName = (id: string) => subjects.data?.items.find((s) => s.subject_id === id)?.name ?? "Subject";
  const toggle = (vid: string) => setPicked((c) => (c.includes(vid) ? c.filter((x) => x !== vid) : [...c, vid]));
  const ready = f.name.trim().length >= 3 && Number(f.duration) > 0 && Number(f.duration) <= 720 && picked.length > 0;
  return (
    <Modal title={`Edit draft: ${test.name}`} onClose={onClose}>
      {error && <Alert kind="error">{error}</Alert>}
      <Field label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <TextAreaField label="Description (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <div className="grid cols-3">
        <Field label="Exam type" value={f.exam_type} onChange={(e) => setF({ ...f, exam_type: e.target.value })} placeholder="NEET" />
        <ClassTargetSelect label="Class" value={f.target_class} onChange={(v) => setF({ ...f, target_class: v })} />
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
          onDone(await api<GlobalTest>(`/api/v1/global-tests/${test.test_id}`, { method: "PUT", body }));
        })}>Save draft</Button>
      </div>
    </Modal>
  );
}
