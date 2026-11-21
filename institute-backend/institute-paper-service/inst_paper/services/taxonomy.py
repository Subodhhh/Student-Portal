"""Canonical question taxonomy: a provider's Chapter -> Topic -> Subtopic records per subject, and approved aliases.

Business rules (owner decisions, docs/institute-portal/BULK_QUESTION_IMPORT_PLAN.md section 4):
  * Records are provider-scoped and form the hierarchy Subject -> Chapter -> Topic -> Subtopic.
  * Two labels that differ only in capitalisation, spacing or Unicode width are the SAME record (core.taxonomy.label_key).
  * A typed value matches a record by its name or by an approved alias - never by similarity. Similar names are only offered as
    suggestions; an administrator decides (map with an alias, or create a record).
  * Nothing here creates a record on its own: records and aliases are created only through the explicit functions below, which the
    API allows to holders of `taxonomy.manage`.
  * Records are never deleted. Retiring hides one from new classification; questions already linked keep their link.

The functions raise TaxonomyError for anything the caller must correct; routes turn it into an HTTP error with the same message.
"""
import difflib
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from institute_common.core.taxonomy import LEVEL_MAX, TAXONOMY_LEVELS, clean_label, label_key
from institute_common.database import utcnow
from institute_common.models.papers import Question, QuestionTaxonomyAlias, QuestionTaxonomyNode

PARENT_LEVEL = {"CHAPTER": None, "TOPIC": "CHAPTER", "SUBTOPIC": "TOPIC"}
LINK_COLUMN = {"CHAPTER": "chapter_id", "TOPIC": "topic_id", "SUBTOPIC": "subtopic_id"}     # bank_questions columns
LABEL_COLUMN = {"CHAPTER": "chapter", "TOPIC": "topic", "SUBTOPIC": "subtopic"}
SUGGESTION_LIMIT = 5
SUGGESTION_CUTOFF = 0.6           # difflib similarity (0..1) below which a name is not worth suggesting


class TaxonomyError(Exception):
    """A request the taxonomy rules refuse. `status`/`code` become the HTTP error; the message is safe to show to users."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ------------------------------------------------------------------ names

def clean_name(level: str, value: str | None) -> tuple[str, str]:
    """(display name, comparison key) of a typed name; TaxonomyError when it is blank or not a valid label for this level."""
    try:
        name = clean_label(value, LEVEL_MAX[level])
    except ValueError as exc:
        raise TaxonomyError(422, "INVALID_NAME", f"The {level.lower()} name {exc}.") from exc
    if name is None:
        raise TaxonomyError(422, "INVALID_NAME", f"Enter the {level.lower()} name.")
    return name, label_key(name)


def _place(stmt, model, provider_id: UUID, subject_id: UUID, parent_id: UUID | None):
    """Restrict a query on nodes or aliases to one place in the tree (same rule as the unique indexes)."""
    if parent_id is None:
        return stmt.where(model.provider_id == provider_id, model.subject_id == subject_id, model.parent_id.is_(None))
    return stmt.where(model.parent_id == parent_id)


# ------------------------------------------------------------------ matching (no writes)

def _key_or_none(value: str | None) -> str | None:
    """The comparison key of a typed value; None when blank or not a valid label (an invalid label simply matches nothing)."""
    try:
        return label_key(value) if value else None
    except ValueError:
        return None


def find(db: Session, provider_id: UUID, subject_id: UUID, level: str, parent_id: UUID | None,
         value: str | None) -> tuple[QuestionTaxonomyNode | None, str | None]:
    """The ACTIVE record a typed value means at one place in the tree: (record, "NAME" | "ALIAS"), or (None, None).
    Matching is exact on the normalised key; similarity never decides."""
    key = _key_or_none(value)
    if key is None:
        return None, None
    by_name = db.scalar(_place(select(QuestionTaxonomyNode), QuestionTaxonomyNode, provider_id, subject_id, parent_id)
                        .where(QuestionTaxonomyNode.level == level, QuestionTaxonomyNode.name_key == key,
                               QuestionTaxonomyNode.status == "ACTIVE"))
    if by_name:
        return by_name, "NAME"
    by_alias = db.scalar(_place(select(QuestionTaxonomyNode).join(QuestionTaxonomyAlias, QuestionTaxonomyAlias.node_id == QuestionTaxonomyNode.node_id),
                                QuestionTaxonomyAlias, provider_id, subject_id, parent_id)
                         .where(QuestionTaxonomyNode.level == level, QuestionTaxonomyAlias.alias_key == key,
                                QuestionTaxonomyNode.status == "ACTIVE"))
    if by_alias:
        return by_alias, "ALIAS"
    return None, None


def suggestions(db: Session, provider_id: UUID, subject_id: UUID, level: str, parent_id: UUID | None,
                value: str | None) -> list[QuestionTaxonomyNode]:
    """Up to 5 ACTIVE records at the same place whose name or an alias looks like `value` (shown to the user, never applied)."""
    key = _key_or_none(value)
    if key is None:
        return []
    nodes = db.scalars(_place(select(QuestionTaxonomyNode), QuestionTaxonomyNode, provider_id, subject_id, parent_id)
                       .where(QuestionTaxonomyNode.level == level, QuestionTaxonomyNode.status == "ACTIVE")).all()
    keys: dict[str, QuestionTaxonomyNode] = {n.name_key: n for n in nodes}
    by_id = {n.node_id: n for n in nodes}
    for alias in db.scalars(select(QuestionTaxonomyAlias).where(QuestionTaxonomyAlias.node_id.in_(list(by_id) or [None]))):
        keys.setdefault(alias.alias_key, by_id[alias.node_id])
    out: list[QuestionTaxonomyNode] = []
    for close in difflib.get_close_matches(key, list(keys), n=SUGGESTION_LIMIT * 2, cutoff=SUGGESTION_CUTOFF):
        if keys[close] not in out:
            out.append(keys[close])
    return out[:SUGGESTION_LIMIT]


# ------------------------------------------------------------------ changes (callers authorise first)

def _parent(db: Session, provider_id: UUID, subject_id: UUID, level: str, parent_id: UUID | None) -> QuestionTaxonomyNode | None:
    """Check that `parent_id` is a valid parent for a new record of `level` (TaxonomyError otherwise)."""
    wanted = PARENT_LEVEL[level]
    if wanted is None:
        if parent_id is not None:
            raise TaxonomyError(422, "INVALID_PARENT", "A chapter has no parent.")
        return None
    parent = db.get(QuestionTaxonomyNode, parent_id) if parent_id else None
    # the same answer for "missing" and "another provider's": never reveal other providers' records
    if parent is None or parent.provider_id != provider_id or parent.subject_id != subject_id or parent.level != wanted:
        raise TaxonomyError(422, "INVALID_PARENT", f"A {level.lower()} needs a {wanted.lower()} of the same subject as its parent.")
    if parent.status != "ACTIVE":
        raise TaxonomyError(422, "INVALID_PARENT", f"The {wanted.lower()} is retired.")
    return parent


def _refuse_taken(db: Session, provider_id: UUID, subject_id: UUID, level: str, parent_id: UUID | None, key: str,
                  ignore_node: UUID | None = None) -> None:
    """A name or alias must be free at its place in the tree: not another record's name (active or retired) nor an alias."""
    taken = db.scalar(_place(select(QuestionTaxonomyNode), QuestionTaxonomyNode, provider_id, subject_id, parent_id)
                      .where(QuestionTaxonomyNode.level == level, QuestionTaxonomyNode.name_key == key))
    if taken and taken.node_id != ignore_node:
        raise TaxonomyError(409, "NAME_TAKEN", f"“{taken.name}” already exists here" + (" (retired)." if taken.status != "ACTIVE" else "."))
    alias = db.scalar(_place(select(QuestionTaxonomyAlias), QuestionTaxonomyAlias, provider_id, subject_id, parent_id)
                      .where(QuestionTaxonomyAlias.alias_key == key))
    if alias:
        raise TaxonomyError(409, "NAME_TAKEN", f"“{alias.alias}” is already an alias of another record here.")


def create_node(db: Session, *, provider_id: UUID, subject_id: UUID, level: str, parent_id: UUID | None, name: str,
                staff_id: UUID, ip: str | None) -> QuestionTaxonomyNode:
    """Create one record (flushed, not committed). TaxonomyError for an invalid name or parent, or a name already used here."""
    if level not in TAXONOMY_LEVELS:
        raise TaxonomyError(422, "INVALID_LEVEL", "Level must be CHAPTER, TOPIC or SUBTOPIC.")
    _parent(db, provider_id, subject_id, level, parent_id)
    name, key = clean_name(level, name)
    _refuse_taken(db, provider_id, subject_id, level, parent_id, key)
    node = QuestionTaxonomyNode(provider_id=provider_id, subject_id=subject_id, parent_id=parent_id, level=level, name=name,
                                name_key=key, created_by=staff_id, created_ip=ip)
    db.add(node)
    db.flush()
    return node


def add_alias(db: Session, node: QuestionTaxonomyNode, alias: str, staff_id: UUID, ip: str | None) -> QuestionTaxonomyAlias:
    """Approve another spelling for `node` (flushed, not committed). Refused when it is the record's own name or already used."""
    if node.status != "ACTIVE":
        raise TaxonomyError(409, "RECORD_RETIRED", "Aliases can only be added to an active record.")
    text, key = clean_name(node.level, alias)
    if key == node.name_key:
        raise TaxonomyError(409, "NAME_TAKEN", "That is already the record's own name.")
    _refuse_taken(db, node.provider_id, node.subject_id, node.level, node.parent_id, key)
    row = QuestionTaxonomyAlias(node_id=node.node_id, provider_id=node.provider_id, subject_id=node.subject_id, parent_id=node.parent_id,
                                alias=text, alias_key=key, approved_by=staff_id, approved_ip=ip)
    db.add(row)
    db.flush()
    return row


def rename(db: Session, node: QuestionTaxonomyNode, new_name: str, staff_id: UUID) -> int:
    """Rename a record and copy the new name into the labels of the questions linked to it. Returns how many questions changed.
    Question CONTENT is untouched (labels are classification metadata; versions stay immutable)."""
    name, key = clean_name(node.level, new_name)
    _refuse_taken(db, node.provider_id, node.subject_id, node.level, node.parent_id, key, ignore_node=node.node_id)
    node.name, node.name_key, node.updated_by, node.updated_at = name, key, staff_id, utcnow()
    link, label = getattr(Question, LINK_COLUMN[node.level]), LABEL_COLUMN[node.level]
    changed = db.execute(update(Question).where(link == node.node_id).values({label: name}).execution_options(synchronize_session=False))
    db.flush()
    return changed.rowcount or 0


def retire(db: Session, node: QuestionTaxonomyNode, staff_id: UUID) -> None:
    """Hide a record from new classification. Refused while it still has active children (retire those first)."""
    child = db.scalar(select(QuestionTaxonomyNode.node_id).where(QuestionTaxonomyNode.parent_id == node.node_id,
                                                                 QuestionTaxonomyNode.status == "ACTIVE").limit(1))
    if child:
        raise TaxonomyError(409, "HAS_ACTIVE_CHILDREN", "Retire its topics/subtopics first.")
    node.status, node.updated_by, node.updated_at = "RETIRED", staff_id, utcnow()
    db.flush()


# ------------------------------------------------------------------ questions

def link_question(db: Session, q: Question) -> None:
    """Point a PROVIDER question's chapter_id/topic_id/subtopic_id at the records its labels name (by name or alias), and write the
    records' canonical names into its labels. A label that matches nothing keeps its text and gets no link - nothing is created.
    The hierarchy is followed: a topic is looked up only under the matched chapter, a subtopic only under the matched topic."""
    if q.provider_id is None:
        q.chapter_id = q.topic_id = q.subtopic_id = None        # GLOBAL (platform) questions have no provider taxonomy yet
        return
    parent_id = None
    for level in TAXONOMY_LEVELS:
        label = LABEL_COLUMN[level]
        node = None
        if parent_id is not None or level == "CHAPTER":
            node, _how = find(db, q.provider_id, q.subject_id, level, parent_id, getattr(q, label))
        setattr(q, LINK_COLUMN[level], node.node_id if node else None)
        if node:
            setattr(q, label, node.name)
        parent_id = node.node_id if node else None
