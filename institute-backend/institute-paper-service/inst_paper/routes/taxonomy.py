"""Question taxonomy API: a provider's canonical Chapters, Topics and Subtopics per subject - or the PLATFORM list (no provider_id),
used by platform questions - their aliases, matching and suggestions, alias requests, merging, and the "Unlinked names" queue
(docs/institute-portal/TAXONOMY_ALIASES_PLAN.md).

Reading needs `question.read` for the provider (any of its branches; any staff for the platform list); every change, approving an alias
request and resolving unlinked names need `taxonomy.manage` for the provider (Provider Admins) - for the platform list, platform
administrators. Anyone who may read may REQUEST an alias. Records of other providers answer 404, exactly like records that do not
exist. Every change is audited. The rules themselves live in inst_paper.services.taxonomy.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.papers import Question, QuestionTaxonomyAlias, QuestionTaxonomyAliasRequest, QuestionTaxonomyNode
from institute_common.util import Strict

from inst_paper.services import bank_access, notify, taxonomy
from inst_paper.services.taxonomy import TaxonomyError

router = APIRouter(prefix="/api/v1", tags=["Question taxonomy"])

Level = Literal["CHAPTER", "TOPIC", "SUBTOPIC"]


class NodeIn(Strict):
    provider_id: UUID | None = None            # None: the platform list (platform administrators)
    subject_id: UUID
    level: Level
    parent_id: UUID | None = None
    name: str = Field(max_length=300)          # trimmed and validated by the service (per-level limit)


class NodePatch(Strict):
    name: str | None = Field(default=None, max_length=300)
    status: Literal["RETIRED", "ACTIVE"] | None = None   # records are never deleted: retire, or restore a retired one


class AliasIn(Strict):
    alias: str = Field(max_length=300)


class AliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    alias_id: UUID
    alias: str
    approved_by: UUID
    approved_at: datetime


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    node_id: UUID
    provider_id: UUID | None
    subject_id: UUID
    parent_id: UUID | None
    level: str
    name: str
    status: str
    merged_into_id: UUID | None = None
    aliases: list[AliasOut] = []


class MatchOut(BaseModel):
    match: NodeOut | None
    matched_by: Literal["NAME", "ALIAS"] | None
    suggestions: list[NodeOut]


# ------------------------------------------------------------------ helpers

def _stored_ip(p: Principal) -> str | None:
    """The caller's IP for an inet column: None when it is unknown (the same rule as review drafts)."""
    return p.ip if p.ip != "unknown" else None


def _raise(exc: TaxonomyError):
    return api_error(exc.status, exc.code, exc.message)


def _require_subject(db: Session, provider_id: UUID | None, subject_id: UUID) -> None:
    if provider_id is None:
        subject = db.get(Subject, subject_id)
        if subject is None or subject.status != "ACTIVE":
            raise api_error(422, "SUBJECT_NOT_AVAILABLE", "Choose an active subject of the platform catalogue.")
        return
    if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id, ProviderSubject.subject_id == subject_id)):
        raise api_error(422, "SUBJECT_NOT_ENABLED", "Subject is not enabled for this provider.")


def _require_read(db: Session, p: Principal, provider_id: UUID | None) -> None:
    """Reading a provider's taxonomy: its staff with question.read - the Provider Admin, its teachers and (the taxonomy is shared by all
    branches, like the questions it classifies) its Branch Admins - and platform roles. The platform list: any staff with question.read."""
    if provider_id is None:
        if not p.has_perm("question.read"):
            p.require(db, "question.read")
        return
    if not (p.can("question.read", provider_id=provider_id) or bank_access.readable_subjects(p, provider_id) is None):
        p.require(db, "question.read", provider_id=provider_id)


def may_manage(p: Principal, provider_id: UUID | None) -> bool:
    """taxonomy.manage for this list: the provider's Provider Admin (and platform roles, as before); the platform list: platform
    administrators only."""
    if provider_id is None:
        return p.is_global and p.has_perm("taxonomy.manage")
    return p.can("taxonomy.manage", provider_id=provider_id)


def _require_manage(db: Session, p: Principal, provider_id: UUID | None) -> None:
    if not may_manage(p, provider_id):
        if provider_id is None:
            audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "taxonomy.manage", "list": "platform"})
            raise api_error(403, "FORBIDDEN", "Only platform administrators manage the platform list.")
        p.require(db, "taxonomy.manage", provider_id=provider_id)


def _node(db: Session, p: Principal, node_id: UUID, permission: str) -> QuestionTaxonomyNode:
    """The record, if the caller holds `permission` for its list; 404 otherwise (no probing of other providers' records)."""
    node = db.get(QuestionTaxonomyNode, node_id)
    allowed = node is not None and (may_manage(p, node.provider_id) if permission == "taxonomy.manage" else _readable(p, node.provider_id))
    if node is None or not allowed:
        if node is not None:
            audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=node.provider_id,
                         details={"permission": permission, "node_id": str(node_id)})
        raise api_error(404, "NOT_FOUND", "Taxonomy record not found.")
    return node


def _readable(p: Principal, provider_id: UUID | None) -> bool:
    if provider_id is None:
        return p.has_perm("question.read")
    return p.can("question.read", provider_id=provider_id) or bank_access.readable_subjects(p, provider_id) is None


def _outs(db: Session, nodes: list[QuestionTaxonomyNode]) -> list[NodeOut]:
    aliases: dict[UUID, list] = {}
    ids = [n.node_id for n in nodes]
    if ids:
        for a in db.scalars(select(QuestionTaxonomyAlias).where(QuestionTaxonomyAlias.node_id.in_(ids)).order_by(QuestionTaxonomyAlias.alias)):
            aliases.setdefault(a.node_id, []).append(AliasOut.model_validate(a))
    return [NodeOut.model_validate(n).model_copy(update={"aliases": aliases.get(n.node_id, [])}) for n in nodes]


def _audit(db: Session, p: Principal, action: str, node: QuestionTaxonomyNode, **details) -> None:
    audit.record(db, action, "SUCCESS", actor=p.staff_id, target_type="question_taxonomy_node", target_id=node.node_id,
                 provider_id=node.provider_id, ip=p.ip, details={"level": node.level, "name": node.name, **details})


def _commit_or_conflict(db: Session) -> None:
    """Commit; a concurrent request that created the same name wins the unique index and this one gets the usual 409."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "NAME_TAKEN", "That name was just created here by someone else.")


# ------------------------------------------------------------------ read

@router.get("/question-taxonomy/nodes", response_model=list[NodeOut])
def list_nodes(subject_id: UUID, provider_id: UUID | None = None, level: Level | None = None, parent_id: UUID | None = None,
               include_retired: bool = False, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A provider's records of one subject - or the platform list without provider_id - (optionally one level and/or the children
    of one record), sorted by name. Merged records are left out (their names are aliases of the record they were merged into)."""
    _require_read(db, p, provider_id)
    stmt = select(QuestionTaxonomyNode).where(taxonomy.owner(QuestionTaxonomyNode, provider_id), QuestionTaxonomyNode.subject_id == subject_id,
                                              QuestionTaxonomyNode.status != "MERGED")
    if level:
        stmt = stmt.where(QuestionTaxonomyNode.level == level)
    if parent_id:
        stmt = stmt.where(QuestionTaxonomyNode.parent_id == parent_id)
    if not include_retired:
        stmt = stmt.where(QuestionTaxonomyNode.status == "ACTIVE")
    return _outs(db, list(db.scalars(stmt.order_by(QuestionTaxonomyNode.level, QuestionTaxonomyNode.name).limit(2000))))


@router.get("/question-taxonomy/match", response_model=MatchOut)
def match(subject_id: UUID, level: Level, value: str = Query(max_length=300), parent_id: UUID | None = None,
          provider_id: UUID | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """What a typed value means at one place in the tree: the record matched by name or approved alias, otherwise suggestions only
    (similar names are never applied automatically)."""
    _require_read(db, p, provider_id)
    node, how = taxonomy.find(db, provider_id, subject_id, level, parent_id, value)
    suggested = [] if node else taxonomy.suggestions(db, provider_id, subject_id, level, parent_id, value)
    outs = _outs(db, ([node] if node else []) + suggested)
    return MatchOut(match=outs[0] if node else None, matched_by=how, suggestions=outs[1:] if node else outs)


# ------------------------------------------------------------------ change (taxonomy.manage)

@router.post("/question-taxonomy/nodes", response_model=NodeOut, status_code=201)
def create_node(body: NodeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_manage(db, p, body.provider_id)
    _require_subject(db, body.provider_id, body.subject_id)
    try:
        node = taxonomy.create_node(db, provider_id=body.provider_id, subject_id=body.subject_id, level=body.level,
                                    parent_id=body.parent_id, name=body.name, staff_id=p.staff_id, ip=_stored_ip(p))
    except TaxonomyError as exc:
        db.rollback()
        raise _raise(exc)
    except IntegrityError:                    # a concurrent request took the same name at the same place
        db.rollback()
        raise api_error(409, "NAME_TAKEN", "That name was just created here by someone else.")
    _commit_or_conflict(db)
    _audit(db, p, "taxonomy.create", node)
    return _outs(db, [node])[0]


@router.patch("/question-taxonomy/nodes/{node_id}", response_model=NodeOut)
def update_node(node_id: UUID, body: NodePatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Rename (the linked questions' labels follow) and/or retire a record."""
    node = _node(db, p, node_id, "taxonomy.manage")
    if body.name is None and body.status is None:
        raise api_error(422, "NO_CHANGE", "Give a new name, or retire or restore the record.")
    old_name, renamed = node.name, 0
    try:
        if body.name is not None:
            renamed = taxonomy.rename(db, node, body.name, p.staff_id)
        if body.status == "RETIRED":
            taxonomy.retire(db, node, p.staff_id)
        elif body.status == "ACTIVE" and node.status != "ACTIVE":
            taxonomy.restore(db, node, p.staff_id)
    except TaxonomyError as exc:
        db.rollback()
        raise _raise(exc)
    except IntegrityError:                    # a concurrent request took the same name at the same place
        db.rollback()
        raise api_error(409, "NAME_TAKEN", "That name was just created here by someone else.")
    _commit_or_conflict(db)
    _audit(db, p, "taxonomy.update", node, previous_name=old_name, status=node.status, questions_relabelled=renamed)
    return _outs(db, [node])[0]


@router.post("/question-taxonomy/nodes/{node_id}/aliases", response_model=NodeOut, status_code=201)
def add_alias(node_id: UUID, body: AliasIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Approve another spelling for a record; later imports and edits that use it are linked to the record."""
    node = _node(db, p, node_id, "taxonomy.manage")
    try:
        alias = taxonomy.add_alias(db, node, body.alias, p.staff_id, _stored_ip(p))
    except TaxonomyError as exc:
        db.rollback()
        raise _raise(exc)
    except IntegrityError:                    # a concurrent request took the same name at the same place
        db.rollback()
        raise api_error(409, "NAME_TAKEN", "That name was just created here by someone else.")
    _commit_or_conflict(db)
    _audit(db, p, "taxonomy.alias", node, alias=alias.alias)
    return _outs(db, [node])[0]


# ------------------------------------------------------------------ suggestions, tree (read)

class SuggestionOut(BaseModel):
    node_id: UUID
    name: str
    score: float
    band: Literal["HIGH", "MEDIUM"]
    matched: str                               # the name or approved alias the typed value resembles
    parent_id: UUID | None = None
    parent_name: str | None = None             # for "elsewhere": the chapter (or topic) it belongs to


class SuggestOut(BaseModel):
    match: NodeOut | None
    matched_by: Literal["NAME", "ALIAS"] | None
    preselect: UUID | None                     # the one clearly meant: shown pre-selected, still confirmed (decision A1)
    suggestions: list[SuggestionOut]
    elsewhere: list[SuggestionOut]             # the same name at another place in the tree
    in_use: bool                               # this place has records: a typed name must be one of them


def _with_parent_names(db: Session, items: list[dict]) -> list[SuggestionOut]:
    parents = {str(n.node_id): n.name for n in db.scalars(select(QuestionTaxonomyNode).where(
        QuestionTaxonomyNode.node_id.in_([UUID(i["parent_id"]) for i in items if i.get("parent_id")] or [None])))}
    return [SuggestionOut(**i, parent_name=parents.get(i.get("parent_id") or "")) for i in items]


@router.get("/question-taxonomy/suggest", response_model=SuggestOut)
def suggest(subject_id: UUID, level: Level, value: str = Query(max_length=300), parent_id: UUID | None = None,
            provider_id: UUID | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """What a typed Chapter / Topic / Subtopic may mean ("Did you mean …?"), for the forms as the user types. Never applies anything."""
    _require_read(db, p, provider_id)
    result = taxonomy.suggest(db, provider_id, subject_id, level, parent_id, value)
    payload = taxonomy.suggestion_payload(result)
    return SuggestOut(match=_outs(db, [result["match"]])[0] if result["match"] is not None else None, matched_by=result["how"],
                      preselect=payload["preselect"], suggestions=_with_parent_names(db, payload["suggestions"]),
                      elsewhere=_with_parent_names(db, payload["elsewhere"]),
                      in_use=taxonomy.in_use(db, provider_id, subject_id, level, parent_id))


class TreeNodeOut(NodeOut):
    questions: int = 0
    children: list["TreeNodeOut"] = []


@router.get("/question-taxonomy/tree", response_model=list[TreeNodeOut])
def tree(subject_id: UUID, provider_id: UUID | None = None, include_retired: bool = False,
         p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The whole Chapter -> Topic -> Subtopic tree of one subject (a provider's, or the platform list), each record with its aliases
    and how many active questions are filed under it."""
    _require_read(db, p, provider_id)
    stmt = select(QuestionTaxonomyNode).where(taxonomy.owner(QuestionTaxonomyNode, provider_id), QuestionTaxonomyNode.subject_id == subject_id,
                                              QuestionTaxonomyNode.status != "MERGED")
    if not include_retired:
        stmt = stmt.where(QuestionTaxonomyNode.status == "ACTIVE")
    nodes = list(db.scalars(stmt.order_by(QuestionTaxonomyNode.name).limit(5000)))
    counts: dict = {}
    for level in ("CHAPTER", "TOPIC", "SUBTOPIC"):
        link = getattr(Question, taxonomy.LINK_COLUMN[level])
        counts.update(dict(db.execute(select(link, func.count()).where(link.in_([n.node_id for n in nodes if n.level == level] or [None]),
                                                                       Question.status == "ACTIVE").group_by(link)).all()))
    outs = {o.node_id: TreeNodeOut(**o.model_dump(), questions=counts.get(o.node_id, 0)) for o in _outs(db, nodes)}
    roots = []
    for n in nodes:
        parent = outs.get(n.parent_id) if n.parent_id else None
        (parent.children if parent else roots).append(outs[n.node_id])         # under a retired (hidden) parent: shown at the top
    return roots


# ------------------------------------------------------------------ merge, aliases (taxonomy.manage)

class MergeIn(Strict):
    into_id: UUID


@router.post("/question-taxonomy/nodes/{node_id}/merge", response_model=NodeOut)
def merge(node_id: UUID, body: MergeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Fold a duplicate into the real record: its questions and children move there and its name becomes an alias of it."""
    node = _node(db, p, node_id, "taxonomy.manage")
    into = _node(db, p, body.into_id, "taxonomy.manage")
    try:
        moved = taxonomy.merge(db, node, into, p.staff_id, _stored_ip(p))
    except TaxonomyError as exc:
        db.rollback()
        raise _raise(exc)
    _commit_or_conflict(db)
    _audit(db, p, "taxonomy.merge", node, into_id=str(into.node_id), into_name=into.name, **moved)
    return _outs(db, [into])[0]


@router.delete("/question-taxonomy/aliases/{alias_id}", response_model=NodeOut)
def remove_alias(alias_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    alias = db.get(QuestionTaxonomyAlias, alias_id)
    node = _node(db, p, alias.node_id, "taxonomy.manage") if alias else None
    if alias is None or node is None:
        raise api_error(404, "NOT_FOUND", "Alias not found.")
    text = alias.alias
    taxonomy.remove_alias(db, alias)
    db.commit()
    _audit(db, p, "taxonomy.alias_removed", node, alias=text)
    return _outs(db, [node])[0]


# ------------------------------------------------------------------ alias requests

class AliasRequestIn(Strict):
    node_id: UUID
    alias: str = Field(max_length=300)
    source: Literal["BULK_UPLOAD", "QUESTION", "CLASSIFY", "UNLINKED"]
    source_ref: str | None = Field(default=None, max_length=64)


class AliasRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    request_id: UUID
    node_id: UUID
    node_name: str | None = None
    level: str | None = None
    provider_id: UUID | None
    subject_id: UUID
    alias: str
    source: str
    source_ref: str | None
    status: str
    requested_by: UUID
    requested_at: datetime
    decided_by: UUID | None
    decided_at: datetime | None
    decision_reason: str | None


class DecisionIn(Strict):
    reason: str | None = Field(default=None, max_length=500)


def _request_out(db: Session, r: QuestionTaxonomyAliasRequest) -> AliasRequestOut:
    node = db.get(QuestionTaxonomyNode, r.node_id)
    return AliasRequestOut.model_validate(r).model_copy(update={"node_name": node.name if node else None, "level": node.level if node else None})


@router.post("/question-taxonomy/alias-requests", status_code=201)
def request_alias(body: AliasRequestIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """"Remember this spelling": approved at once for the record's administrators (returns the record), a request for everyone else
    (returns the request). Nothing is created when it already is the record's name or alias."""
    node = _node(db, p, body.node_id, "question.read")
    try:
        if may_manage(p, node.provider_id):
            taxonomy.add_alias(db, node, body.alias, p.staff_id, _stored_ip(p))
            _commit_or_conflict(db)
            _audit(db, p, "taxonomy.alias", node, alias=body.alias.strip(), source=body.source)
            return {"approved": True, "node": _outs(db, [node])[0]}
        req = taxonomy.request_alias(db, node, body.alias, p.staff_id, body.source, body.source_ref)
    except TaxonomyError as exc:
        db.rollback()
        raise _raise(exc)
    _commit_or_conflict(db)
    if req is not None:
        _audit(db, p, "taxonomy.alias_requested", node, alias=req.alias, source=body.source)
    return {"approved": False, "request": _request_out(db, req) if req else None}


@router.get("/question-taxonomy/alias-requests", response_model=list[AliasRequestOut])
def alias_requests(provider_id: UUID | None = None, status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING",
                   p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The requests of one list for its administrators; everyone else sees their own requests."""
    _require_read(db, p, provider_id)
    stmt = select(QuestionTaxonomyAliasRequest).where(taxonomy.owner(QuestionTaxonomyAliasRequest, provider_id),
                                                      QuestionTaxonomyAliasRequest.status == status)
    if not may_manage(p, provider_id):
        stmt = stmt.where(QuestionTaxonomyAliasRequest.requested_by == p.staff_id)
    return [_request_out(db, r) for r in db.scalars(stmt.order_by(QuestionTaxonomyAliasRequest.requested_at.desc()).limit(500))]


def _decide(request_id: UUID, approve: bool, body: DecisionIn, p: Principal, db: Session) -> AliasRequestOut:
    req = db.get(QuestionTaxonomyAliasRequest, request_id)
    if req is None or not may_manage(p, req.provider_id):
        raise api_error(404, "NOT_FOUND", "Request not found.")
    node = db.get(QuestionTaxonomyNode, req.node_id)
    try:
        taxonomy.decide_request(db, req, approve, p.staff_id, _stored_ip(p), body.reason)
    except TaxonomyError as exc:
        db.rollback()
        raise _raise(exc)
    verdict = "approved" if approve else "not approved"
    notify.queue(db, [req.requested_by], kind="TAXONOMY_ALIAS_DECIDED", dedupe=f"alias-request:{req.request_id}", provider_id=req.provider_id,
                 title=f"Spelling {verdict}: {req.alias}",
                 body=(f"“{req.alias}” is now recognised as {node.level.lower()} “{node.name}”." if approve else
                       f"“{req.alias}” was not added as another name of “{node.name}”." + (f" Reason: {req.decision_reason}" if req.decision_reason else "")))
    _commit_or_conflict(db)
    _audit(db, p, "taxonomy.alias_request_" + ("approved" if approve else "rejected"), node, alias=req.alias, reason=req.decision_reason)
    return _request_out(db, req)


@router.post("/question-taxonomy/alias-requests/{request_id}/approve", response_model=AliasRequestOut)
def approve_request(request_id: UUID, body: DecisionIn | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _decide(request_id, True, body or DecisionIn(), p, db)


@router.post("/question-taxonomy/alias-requests/{request_id}/reject", response_model=AliasRequestOut)
def reject_request(request_id: UUID, body: DecisionIn | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _decide(request_id, False, body or DecisionIn(), p, db)


# ------------------------------------------------------------------ the "Unlinked names" queue (taxonomy.manage)

@router.get("/question-taxonomy/unlinked")
def unlinked(subject_id: UUID, provider_id: UUID | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Names typed on existing questions that are linked to no record, grouped by spelling, with suggestions."""
    _require_manage(db, p, provider_id)
    items = taxonomy.unlinked(db, provider_id, subject_id)
    for item in items:
        item["suggestions"] = [s.model_dump(mode="json") for s in _with_parent_names(db, item["suggestions"])]
        item["elsewhere"] = [s.model_dump(mode="json") for s in _with_parent_names(db, item["elsewhere"])]
    return items


class ResolveIn(Strict):
    subject_id: UUID
    provider_id: UUID | None = None
    level: Level
    parent_id: UUID | None = None
    value: str = Field(max_length=300)
    node_id: UUID
    remember: bool = True                      # also approve the spelling as an alias (future typing is recognised)


@router.post("/question-taxonomy/unlinked/resolve")
def resolve_unlinked(body: ResolveIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Link every question that uses this spelling (at this place) to the chosen record, and optionally remember the spelling."""
    _require_manage(db, p, body.provider_id)
    node = _node(db, p, body.node_id, "taxonomy.manage")
    if (node.provider_id, node.subject_id, node.level, node.parent_id) != (body.provider_id, body.subject_id, body.level, body.parent_id) \
            or node.status != "ACTIVE":
        raise api_error(422, "INVALID_NODE", "Choose an active record of this subject at the same place in the hierarchy.")
    try:
        changed = taxonomy.resolve_unlinked(db, body.provider_id, body.subject_id, body.level, body.parent_id, body.value, node)
        remembered = False
        if body.remember and taxonomy._key_or_none(body.value) != node.name_key:
            try:
                with db.begin_nested():
                    taxonomy.add_alias(db, node, body.value, p.staff_id, _stored_ip(p))
                remembered = True
            except TaxonomyError:
                remembered = False                       # already an alias here: nothing to remember
    except TaxonomyError as exc:
        db.rollback()
        raise _raise(exc)
    _commit_or_conflict(db)
    _audit(db, p, "taxonomy.unlinked_resolved", node, value=body.value, questions=changed, remembered=remembered)
    return {"questions": changed, "remembered": remembered, "node": _outs(db, [node])[0]}
