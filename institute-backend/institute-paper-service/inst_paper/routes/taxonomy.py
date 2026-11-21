"""Question taxonomy API: a provider's canonical Chapters, Topics and Subtopics per subject, their aliases, and matching.

Reading (list, match) needs `question.read` for the provider (any of its branches); every change needs `taxonomy.manage` for the provider (Provider
Admins and platform administrators). Records of other providers answer 404, exactly like records that do not exist. Every change is
audited. The rules themselves live in inst_paper.services.taxonomy.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db
from institute_common.models.academics import ProviderSubject
from institute_common.models.papers import QuestionTaxonomyAlias, QuestionTaxonomyNode
from institute_common.util import Strict

from inst_paper.services import bank_access, taxonomy
from inst_paper.services.taxonomy import TaxonomyError

router = APIRouter(prefix="/api/v1", tags=["Question taxonomy"])

Level = Literal["CHAPTER", "TOPIC", "SUBTOPIC"]


class NodeIn(Strict):
    provider_id: UUID
    subject_id: UUID
    level: Level
    parent_id: UUID | None = None
    name: str = Field(max_length=300)          # trimmed and validated by the service (per-level limit)


class NodePatch(Strict):
    name: str | None = Field(default=None, max_length=300)
    status: Literal["RETIRED"] | None = None   # records are never deleted; retiring is one-way here


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
    provider_id: UUID
    subject_id: UUID
    parent_id: UUID | None
    level: str
    name: str
    status: str
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


def _require_subject(db: Session, provider_id: UUID, subject_id: UUID) -> None:
    if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id, ProviderSubject.subject_id == subject_id)):
        raise api_error(422, "SUBJECT_NOT_ENABLED", "Subject is not enabled for this provider.")


def _require_read(db: Session, p: Principal, provider_id: UUID) -> None:
    """Reading a provider's taxonomy: its staff with question.read - the Provider Admin, its teachers and (the taxonomy is shared by all
    branches, like the questions it classifies) its Branch Admins - and platform roles."""
    if not (p.can("question.read", provider_id=provider_id) or bank_access.readable_subjects(p, provider_id) is None):
        p.require(db, "question.read", provider_id=provider_id)


def _node(db: Session, p: Principal, node_id: UUID, permission: str) -> QuestionTaxonomyNode:
    """The record, if the caller holds `permission` for its provider; 404 otherwise (no probing of other providers' records)."""
    node = db.get(QuestionTaxonomyNode, node_id)
    if node is None or not p.can(permission, provider_id=node.provider_id):
        if node is not None:
            audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=node.provider_id,
                         details={"permission": permission, "node_id": str(node_id)})
        raise api_error(404, "NOT_FOUND", "Taxonomy record not found.")
    return node


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
def list_nodes(provider_id: UUID, subject_id: UUID, level: Level | None = None, parent_id: UUID | None = None,
               include_retired: bool = False, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A provider's records of one subject (optionally one level and/or the children of one record), sorted by name."""
    _require_read(db, p, provider_id)
    stmt = select(QuestionTaxonomyNode).where(QuestionTaxonomyNode.provider_id == provider_id, QuestionTaxonomyNode.subject_id == subject_id)
    if level:
        stmt = stmt.where(QuestionTaxonomyNode.level == level)
    if parent_id:
        stmt = stmt.where(QuestionTaxonomyNode.parent_id == parent_id)
    if not include_retired:
        stmt = stmt.where(QuestionTaxonomyNode.status == "ACTIVE")
    return _outs(db, list(db.scalars(stmt.order_by(QuestionTaxonomyNode.level, QuestionTaxonomyNode.name).limit(2000))))


@router.get("/question-taxonomy/match", response_model=MatchOut)
def match(provider_id: UUID, subject_id: UUID, level: Level, value: str = Query(max_length=300), parent_id: UUID | None = None,
          p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
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
    p.require(db, "taxonomy.manage", provider_id=body.provider_id)
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
        raise api_error(422, "NO_CHANGE", "Give a new name or retire the record.")
    old_name, renamed = node.name, 0
    try:
        if body.name is not None:
            renamed = taxonomy.rename(db, node, body.name, p.staff_id)
        if body.status == "RETIRED":
            taxonomy.retire(db, node, p.staff_id)
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
