import { api } from "./api";
import type { Suggestion, TaxonomyLevel } from "./bulkImports";

/* Chapters, topics and subtopics (paper service, routes/taxonomy.py): a provider's list per subject, or the platform list when no
 * provider is given. Matching only ever suggests; nothing is linked, created or merged without a person choosing it. */

const base = "/api/v1/question-taxonomy";
const q = (params: Record<string, string | null | undefined | boolean>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [k, String(v)])).toString();

export type Alias = { alias_id: string; alias: string };
export type TaxNode = { node_id: string; provider_id: string | null; subject_id: string; parent_id: string | null; level: TaxonomyLevel;
  name: string; status: "ACTIVE" | "RETIRED" | "MERGED"; aliases: Alias[] };
export type TreeNode = TaxNode & { questions: number; children: TreeNode[] };
export type SuggestResult = { match: TaxNode | null; matched_by: "NAME" | "ALIAS" | null; preselect: string | null;
  suggestions: Suggestion[]; elsewhere: Suggestion[]; in_use: boolean };
export type AliasRequest = { request_id: string; node_id: string; node_name: string | null; level: TaxonomyLevel | null; alias: string;
  source: string; status: "PENDING" | "APPROVED" | "REJECTED"; requested_by: string; requested_at: string; decision_reason: string | null };
export type UnlinkedGroup = { level: TaxonomyLevel; value: string; parent_id: string | null; parent_name?: string | null; questions: number;
  suggestions: Suggestion[]; preselect: string | null; elsewhere: Suggestion[] };

export const suggestPath = (providerId: string | null, subjectId: string, level: TaxonomyLevel, value: string, parentId?: string | null) =>
  `${base}/suggest?${q({ provider_id: providerId, subject_id: subjectId, level, value, parent_id: parentId })}`;
export const treePath = (providerId: string | null, subjectId: string, includeRetired = false) =>
  `${base}/tree?${q({ provider_id: providerId, subject_id: subjectId, include_retired: includeRetired || null })}`;
export const requestsPath = (providerId: string | null) => `${base}/alias-requests?${q({ provider_id: providerId })}`;
export const unlinkedPath = (providerId: string | null, subjectId: string) => `${base}/unlinked?${q({ provider_id: providerId, subject_id: subjectId })}`;

export const createNode = (providerId: string | null, subjectId: string, level: TaxonomyLevel, name: string, parentId: string | null) =>
  api<TaxNode>(`${base}/nodes`, { method: "POST", body: { provider_id: providerId, subject_id: subjectId, level, name, parent_id: parentId } });
export const updateNode = (nodeId: string, change: { name?: string; status?: "RETIRED" | "ACTIVE" }) =>
  api<TaxNode>(`${base}/nodes/${nodeId}`, { method: "PATCH", body: change });
export const addAlias = (nodeId: string, alias: string) => api<TaxNode>(`${base}/nodes/${nodeId}/aliases`, { method: "POST", body: { alias } });
export const removeAlias = (aliasId: string) => api<TaxNode>(`${base}/aliases/${aliasId}`, { method: "DELETE" });
export const mergeNode = (nodeId: string, intoId: string) => api<TaxNode>(`${base}/nodes/${nodeId}/merge`, { method: "POST", body: { into_id: intoId } });
/** "Remember this spelling": an alias at once for the list's administrators, a request for everyone else. */
export const requestAlias = (nodeId: string, alias: string, source: "QUESTION" | "CLASSIFY" | "UNLINKED") =>
  api<{ approved: boolean }>(`${base}/alias-requests`, { method: "POST", body: { node_id: nodeId, alias, source } });
export const decideRequest = (requestId: string, approve: boolean, reason?: string) =>
  api<AliasRequest>(`${base}/alias-requests/${requestId}/${approve ? "approve" : "reject"}`, { method: "POST", body: { reason: reason || null } });
export const resolveUnlinked = (providerId: string | null, subjectId: string, g: UnlinkedGroup, nodeId: string, remember: boolean) =>
  api<{ questions: number; remembered: boolean }>(`${base}/unlinked/resolve`, { method: "POST", body: {
    provider_id: providerId, subject_id: subjectId, level: g.level, parent_id: g.parent_id, value: g.value, node_id: nodeId, remember } });
