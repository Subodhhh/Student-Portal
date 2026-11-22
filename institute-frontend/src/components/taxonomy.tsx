import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useScope } from "../auth/ScopeContext";
import { isGlobalRole } from "../auth/roles";
import { useAction, useApi, useReference } from "../hooks/useApi";
import type { Page } from "../services/api";
import type { TaxonomyLevel } from "../services/bulkImports";
import {
  addAlias, createNode, decideRequest, mergeNode, removeAlias, requestsPath, resolveUnlinked, treePath, unlinkedPath, updateNode,
  type AliasRequest, type TreeNode, type UnlinkedGroup,
} from "../services/taxonomy";
import ScopeBar from "./ScopeBar";
import { Alert, Button, Card, EmptyState, Field, Modal, SearchField, SelectField, Spinner, StatusBadge } from "./ui";

/* "Chapters & Topics" (docs/institute-portal/TAXONOMY_ALIASES_PLAN.md §4.3): the official Chapter -> Topic -> Subtopic names of each
 * subject with their other accepted spellings (aliases) and question counts; administrators of the list add, rename, retire, merge
 * duplicates, approve spelling requests and link names typed on old questions. Everyone else sees the list read-only. */

type Subject = { subject_id: string; name: string };
const CHILD: Record<TaxonomyLevel, TaxonomyLevel | null> = { CHAPTER: "TOPIC", TOPIC: "SUBTOPIC", SUBTOPIC: null };
const WORD: Record<TaxonomyLevel, string> = { CHAPTER: "chapter", TOPIC: "topic", SUBTOPIC: "subtopic" };
type Dialog = { kind: "add"; level: TaxonomyLevel; parent: TreeNode | null } | { kind: "rename" | "alias" | "merge"; node: TreeNode };

const flatten = (nodes: TreeNode[]): TreeNode[] => nodes.flatMap((n) => [n, ...flatten(n.children)]);
const matches = (n: TreeNode, text: string): boolean => !text || n.name.toLowerCase().includes(text)
  || n.aliases.some((a) => a.alias.toLowerCase().includes(text)) || n.children.some((c) => matches(c, text));

export function ChaptersTab() {
  const { me, can } = useAuth();
  const global = isGlobalRole(me?.primary_role);
  const scope = useScope();
  const [platform, setPlatform] = useState(global);
  const providerId = platform ? null : scope.providerId || null;
  const subjects = useReference<Page<Subject> | Subject[]>(platform ? "/api/v1/subjects?page_size=100"
    : providerId ? `/api/v1/providers/${providerId}/subjects` : null);
  const list = Array.isArray(subjects.data) ? subjects.data : subjects.data?.items ?? [];
  const [subjectId, setSubjectId] = useState("");
  const subject = list.some((s) => s.subject_id === subjectId) ? subjectId : list[0]?.subject_id ?? "";
  const manager = can("taxonomy.manage") && (!platform || global);
  return (
    <div className="stack">
      <p className="muted small">The official chapter, topic and subtopic names of each subject. Questions are always filed under the official
        name; other spellings (aliases) are only recognised and turned into it.</p>
      <div className="toolbar">
        {global && (
          <SelectField label="List" value={platform ? "platform" : "provider"} onChange={(e) => setPlatform(e.target.value === "platform")}>
            <option value="platform">Platform list (platform questions)</option>
            <option value="provider">A provider's list</option>
          </SelectField>
        )}
        <SelectField label="Subject" value={subject} onChange={(e) => setSubjectId(e.target.value)}>
          {list.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.name}</option>)}
        </SelectField>
      </div>
      {!platform && <ScopeBar branch={false} />}
      {subjects.error && <Alert kind="error">{subjects.error}</Alert>}
      {(platform || providerId) && subject
        ? <SubjectTaxonomy key={`${providerId}-${subject}`} providerId={providerId} subjectId={subject} manager={manager} />
        : subjects.loading ? <Spinner /> : <EmptyState title="No subject to show" hint="Choose a provider that offers subjects." />}
    </div>
  );
}

function SubjectTaxonomy({ providerId, subjectId, manager }: { providerId: string | null; subjectId: string; manager: boolean }) {
  const [retired, setRetired] = useState(false);
  const tree = useApi<TreeNode[]>(treePath(providerId, subjectId, retired));
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const act = useAction();
  const text = search.trim().toLowerCase();
  const all = flatten(tree.data ?? []);
  const change = (run: () => Promise<unknown>) => void act.run(async () => { await run(); tree.reload(); });
  return (
    <>
      <Card>
        <div className="toolbar">
          <SearchField label="Find a name" value={search} onChange={setSearch} placeholder="Name or other spelling" />
          <label className="row checkbox-field"><input type="checkbox" checked={retired} onChange={(e) => setRetired(e.target.checked)} /> Show retired</label>
          {manager && <Button onClick={() => setDialog({ kind: "add", level: "CHAPTER", parent: null })}>Add chapter</Button>}
        </div>
        {act.error && <Alert kind="error">{act.error}</Alert>}
        {tree.error && <Alert kind="error">{tree.error}</Alert>}
        {tree.loading && !tree.data ? <Spinner /> : !tree.data?.length ? (
          <EmptyState title="No chapters yet" hint={manager ? "Add the first chapter of this subject." : "An administrator sets up the chapters."} />
        ) : (
          <ul className="taxonomy-tree">
            {tree.data.filter((n) => matches(n, text)).map((n) => (
              <NodeItem key={n.node_id} node={n} text={text} manager={manager} busy={act.busy} onDialog={setDialog} onChange={change} />
            ))}
          </ul>
        )}
      </Card>
      {dialog && <NodeDialog dialog={dialog} providerId={providerId} subjectId={subjectId} all={all}
        onClose={() => setDialog(null)} onDone={() => { setDialog(null); tree.reload(); }} />}
      <AliasRequests providerId={providerId} manager={manager} onDecided={tree.reload} />
      {manager && <UnlinkedNames providerId={providerId} subjectId={subjectId} all={all} onResolved={tree.reload} />}
    </>
  );
}

function NodeItem({ node, text, manager, busy, onDialog, onChange }: { node: TreeNode; text: string; manager: boolean; busy: boolean;
  onDialog: (d: Dialog) => void; onChange: (run: () => Promise<unknown>) => void }) {
  const child = CHILD[node.level];
  const active = node.status === "ACTIVE";
  return (
    <li>
      <div className="row wrap taxonomy-node">
        <strong>{node.name}</strong>
        <span className="small muted">{WORD[node.level]} · {node.questions} question(s)</span>
        {!active && <StatusBadge status={node.status} />}
        {node.aliases.map((a) => (
          <span key={a.alias_id} className="badge" title="Another accepted spelling">{a.alias}
            {manager && <button type="button" className="link-btn" aria-label={`Remove spelling ${a.alias}`} disabled={busy}
              onClick={() => onChange(() => removeAlias(a.alias_id))}> ×</button>}</span>
        ))}
        {manager && active && (
          <span className="row">
            {child && <Button small variant="secondary" onClick={() => onDialog({ kind: "add", level: child, parent: node })}>Add {WORD[child]}</Button>}
            <Button small variant="secondary" onClick={() => onDialog({ kind: "rename", node })}>Rename</Button>
            <Button small variant="secondary" onClick={() => onDialog({ kind: "alias", node })}>Add spelling</Button>
            <Button small variant="secondary" onClick={() => onDialog({ kind: "merge", node })}>Merge into…</Button>
            <Button small variant="secondary" busy={busy} onClick={() => onChange(() => updateNode(node.node_id, { status: "RETIRED" }))}>Retire</Button>
          </span>
        )}
        {manager && node.status === "RETIRED" && (
          <Button small variant="secondary" busy={busy} onClick={() => onChange(() => updateNode(node.node_id, { status: "ACTIVE" }))}>Restore</Button>
        )}
      </div>
      {node.children.length > 0 && (
        <ul>{node.children.filter((c) => matches(c, text) || node.name.toLowerCase().includes(text)).map((c) => (
          <NodeItem key={c.node_id} node={c} text={text} manager={manager} busy={busy} onDialog={onDialog} onChange={onChange} />
        ))}</ul>
      )}
    </li>
  );
}

function NodeDialog({ dialog, providerId, subjectId, all, onClose, onDone }: { dialog: Dialog; providerId: string | null; subjectId: string;
  all: TreeNode[]; onClose: () => void; onDone: () => void }) {
  const [value, setValue] = useState(dialog.kind === "rename" ? dialog.node.name : "");
  const act = useAction();
  const node = dialog.kind === "add" ? null : dialog.node;
  // a duplicate merges only into an active record of the same level under the same parent
  const targets = node ? all.filter((n) => n.node_id !== node.node_id && n.level === node.level && n.parent_id === node.parent_id && n.status === "ACTIVE") : [];
  const title = dialog.kind === "add" ? `Add ${WORD[dialog.level]}${dialog.parent ? ` to “${dialog.parent.name}”` : ""}`
    : dialog.kind === "rename" ? `Rename “${dialog.node.name}”` : dialog.kind === "alias" ? `Another spelling of “${dialog.node.name}”`
      : `Merge “${dialog.node.name}” into…`;
  const save = () => void act.run(async () => {
    if (dialog.kind === "add") await createNode(providerId, subjectId, dialog.level, value, dialog.parent?.node_id ?? null);
    else if (dialog.kind === "rename") await updateNode(dialog.node.node_id, { name: value });
    else if (dialog.kind === "alias") await addAlias(dialog.node.node_id, value);
    else await mergeNode(dialog.node.node_id, value);
    onDone();
  });
  return (
    <Modal title={title} onClose={onClose}>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {dialog.kind === "merge" ? (
        <>
          <p className="small">Its {node!.questions} question(s), its topics and its other spellings move to the chosen record, and
            “{node!.name}” becomes another spelling of it. Nothing is deleted.</p>
          <SelectField label="Keep" value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="">Choose…</option>{targets.map((n) => <option key={n.node_id} value={n.node_id}>{n.name}</option>)}
          </SelectField>
        </>
      ) : (
        <Field label={dialog.kind === "alias" ? "Spelling" : "Name"} value={value} onChange={(e) => setValue(e.target.value)} maxLength={80} />
      )}
      {dialog.kind === "rename" && <p className="small muted">The questions filed under it show the new name.</p>}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button busy={act.busy} disabled={!value.trim()} onClick={save}>{dialog.kind === "merge" ? "Merge" : "Save"}</Button>
      </div>
    </Modal>
  );
}

function AliasRequests({ providerId, manager, onDecided }: { providerId: string | null; manager: boolean; onDecided: () => void }) {
  const requests = useApi<AliasRequest[]>(requestsPath(providerId));
  const [rejecting, setRejecting] = useState<AliasRequest | null>(null);
  const [reason, setReason] = useState("");
  const act = useAction();
  const decide = (r: AliasRequest, approve: boolean, why?: string) => void act.run(async () => {
    await decideRequest(r.request_id, approve, why);
    setRejecting(null);
    requests.reload();
    onDecided();
  });
  if (!requests.data?.length) return requests.error ? <Alert kind="error">{requests.error}</Alert> : null;
  return (
    <Card>
      <h2>{manager ? "Spelling requests" : "Your spelling requests"}</h2>
      <p className="muted small">{manager ? "Staff asked for these spellings to be recognised as the official name. Approving adds the spelling; the requester is told either way."
        : "Waiting for an administrator."}</p>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      <ul className="plain">
        {requests.data.map((r) => (
          <li key={r.request_id} className="row wrap between">
            <span>“{r.alias}” → {r.level ? WORD[r.level] : ""} “{r.node_name}” <span className="small muted">· from {r.source.toLowerCase().replace("_", " ")}</span></span>
            {manager && (
              <span className="row">
                <Button small busy={act.busy} onClick={() => decide(r, true)}>Approve</Button>
                <Button small variant="secondary" onClick={() => { setReason(""); setRejecting(r); }}>Reject</Button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {rejecting && (
        <Modal title={`Reject “${rejecting.alias}”`} onClose={() => setRejecting(null)}>
          <Field label="Reason (optional, shown to the requester)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
          <div className="modal-actions">
            <Button variant="secondary" onClick={() => setRejecting(null)}>Cancel</Button>
            <Button variant="danger" busy={act.busy} onClick={() => decide(rejecting, false, reason)}>Reject</Button>
          </div>
        </Modal>
      )}
    </Card>
  );
}

function UnlinkedNames({ providerId, subjectId, all, onResolved }: { providerId: string | null; subjectId: string; all: TreeNode[]; onResolved: () => void }) {
  const groups = useApi<UnlinkedGroup[]>(unlinkedPath(providerId, subjectId));
  if (!groups.data?.length) return groups.error ? <Alert kind="error">{groups.error}</Alert> : null;
  return (
    <Card>
      <h2>Names on questions that match no record</h2>
      <p className="muted small">Choose the official name for each spelling: every question using it is filed under that record.</p>
      <ul className="import-issues">
        {groups.data.map((g) => (
          <UnlinkedItem key={`${g.level}|${g.parent_id}|${g.value}`} group={g} providerId={providerId} subjectId={subjectId} all={all}
            onResolved={() => { groups.reload(); onResolved(); }} />
        ))}
      </ul>
    </Card>
  );
}

function UnlinkedItem({ group, providerId, subjectId, all, onResolved }: { group: UnlinkedGroup; providerId: string | null; subjectId: string;
  all: TreeNode[]; onResolved: () => void }) {
  const [nodeId, setNodeId] = useState(group.preselect ?? "");
  const [remember, setRemember] = useState(true);
  const act = useAction();
  const suggested = new Map(group.suggestions.map((s) => [s.node_id, s.band]));
  const here = all.filter((n) => n.level === group.level && n.parent_id === group.parent_id && n.status === "ACTIVE");
  const ordered = [...here.filter((n) => suggested.has(n.node_id)).sort((a, b) =>
    group.suggestions.findIndex((s) => s.node_id === a.node_id) - group.suggestions.findIndex((s) => s.node_id === b.node_id)),
  ...here.filter((n) => !suggested.has(n.node_id))];
  const parent = all.find((n) => n.node_id === group.parent_id)?.name;
  return (
    <li>
      <p><strong>{WORD[group.level]} “{group.value}”</strong>{parent && <span className="muted"> under “{parent}”</span>}
        <span className="muted small"> · {group.questions} question(s)</span></p>
      {act.error && <Alert kind="error">{act.error}</Alert>}
      {ordered.length === 0 ? <p className="small muted">There is no {WORD[group.level]} here yet: add one above first.</p> : (
        <div className="row wrap">
          <SelectField label={`Official ${WORD[group.level]}`} value={nodeId} onChange={(e) => setNodeId(e.target.value)}>
            <option value="">Choose…</option>
            {ordered.map((n) => <option key={n.node_id} value={n.node_id}>{n.name}
              {suggested.get(n.node_id) === "HIGH" ? " (very likely)" : suggested.get(n.node_id) ? " (possible)" : ""}</option>)}
          </SelectField>
          <label className="row checkbox-field"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Remember this spelling</label>
          <Button small disabled={!nodeId} busy={act.busy} onClick={() => void act.run(async () => {
            await resolveUnlinked(providerId, subjectId, group, nodeId, remember);
            onResolved();
          })}>Link {group.questions} question(s)</Button>
        </div>
      )}
    </li>
  );
}
