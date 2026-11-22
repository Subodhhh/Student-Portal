"""Canonical question taxonomy: a provider's Chapter -> Topic -> Subtopic records per subject, and approved aliases.

Business rules (owner decisions, docs/institute-portal/BULK_QUESTION_IMPORT_PLAN.md section 4 and TAXONOMY_ALIASES_PLAN.md):
  * Records are provider-scoped - or the PLATFORM list (provider_id NULL), used by platform questions - and form the hierarchy
    Subject -> Chapter -> Topic -> Subtopic.
  * Two labels that differ only in capitalisation, spacing or Unicode width are the SAME record (core.taxonomy.label_key).
  * A typed value matches a record by its name or by an approved alias - never by similarity. Similar names are only offered as
    suggestions (services/taxonomy_match: one clearly closest is pre-selected, several are listed); a person decides.
  * Aliases are approved by the record's administrators (taxonomy.manage: the Provider Admin; platform administrators for the platform
    list); anyone else's "remember this spelling" is an alias REQUEST for them.
  * A duplicate can be MERGED into another record: its questions and children move there and its name becomes an alias.
  * Nothing here creates a record on its own: records and aliases are created only through the explicit functions below, which the
    API allows to holders of `taxonomy.manage`.
  * Records are never deleted. Retiring hides one from new classification; questions already linked keep their link.

The functions raise TaxonomyError for anything the caller must correct; routes turn it into an HTTP error with the same message.
"""
from uuid import UUID

from sqlalchemy import func, null, select, update
from sqlalchemy.orm import Session

from institute_common.core.taxonomy import LEVEL_MAX, TAXONOMY_LEVELS, clean_label, label_key
from institute_common.database import utcnow
from institute_common.models.papers import Question, QuestionTaxonomyAlias, QuestionTaxonomyAliasRequest, QuestionTaxonomyNode

from inst_paper.services import taxonomy_match

PARENT_LEVEL = {"CHAPTER": None, "TOPIC": "CHAPTER", "SUBTOPIC": "TOPIC"}
LINK_COLUMN = {"CHAPTER": "chapter_id", "TOPIC": "topic_id", "SUBTOPIC": "subtopic_id"}     # bank_questions columns
LABEL_COLUMN = {"CHAPTER": "chapter", "TOPIC": "topic", "SUBTOPIC": "subtopic"}
SUGGESTION_LIMIT = taxonomy_match.LIMIT


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


def owner(model, provider_id: UUID | None):
    """The rows of one provider's list, or of the platform list (provider_id None)."""
    return model.provider_id == provider_id if provider_id else model.provider_id.is_(None)


def _place(stmt, model, provider_id: UUID | None, subject_id: UUID, parent_id: UUID | None):
    """Restrict a query on nodes or aliases to one place in the tree (same rule as the unique indexes)."""
    if parent_id is None:
        return stmt.where(owner(model, provider_id), model.subject_id == subject_id, model.parent_id.is_(None))
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


def _names_at(db: Session, nodes: list[QuestionTaxonomyNode]) -> list[tuple[QuestionTaxonomyNode, str]]:
    """(record, name) for every name and approved alias of `nodes` - what a typed value is compared with."""
    names = [(n, n.name) for n in nodes]
    by_id = {n.node_id: n for n in nodes}
    if by_id:
        for alias in db.scalars(select(QuestionTaxonomyAlias).where(QuestionTaxonomyAlias.node_id.in_(list(by_id)))):
            names.append((by_id[alias.node_id], alias.alias))
    return names


def suggest(db: Session, provider_id: UUID | None, subject_id: UUID, level: str, parent_id: UUID | None, value: str | None) -> dict:
    """What a typed value may mean at one place in the tree (nothing is applied):
    {"match": record or None (exact name / approved alias), "how": "NAME" | "ALIAS" | None,
     "candidates": [(record, score, the name or alias it resembles)] best first, "preselect": the one clearly meant (still confirmed),
     "elsewhere": [(record, score, name)] the same name at another place (a topic of ANOTHER chapter)}."""
    node, how = find(db, provider_id, subject_id, level, parent_id, value)
    out = {"match": node, "how": how, "candidates": [], "preselect": None, "elsewhere": []}
    if node is not None or not _key_or_none(value):
        return out
    here = db.scalars(_place(select(QuestionTaxonomyNode), QuestionTaxonomyNode, provider_id, subject_id, parent_id)
                      .where(QuestionTaxonomyNode.level == level, QuestionTaxonomyNode.status == "ACTIVE")).all()
    ranked = taxonomy_match.rank(value, _names_at(db, list(here)))
    out["candidates"], out["preselect"] = ranked, taxonomy_match.preselect(ranked)
    if level != "CHAPTER":
        others = db.scalars(select(QuestionTaxonomyNode).where(
            owner(QuestionTaxonomyNode, provider_id), QuestionTaxonomyNode.subject_id == subject_id, QuestionTaxonomyNode.level == level,
            QuestionTaxonomyNode.status == "ACTIVE", QuestionTaxonomyNode.parent_id != parent_id if parent_id else True)).all()
        out["elsewhere"] = [r for r in taxonomy_match.rank(value, _names_at(db, list(others))) if r[1] >= taxonomy_match.STRONG]
    return out


def suggestions(db: Session, provider_id: UUID | None, subject_id: UUID, level: str, parent_id: UUID | None,
                value: str | None) -> list[QuestionTaxonomyNode]:
    """The records a typed value may mean at one place, best first (shown to the user, never applied)."""
    return [r[0] for r in suggest(db, provider_id, subject_id, level, parent_id, value)["candidates"]]


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


def create_node(db: Session, *, provider_id: UUID | None, subject_id: UUID, level: str, parent_id: UUID | None, name: str,
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


def restore(db: Session, node: QuestionTaxonomyNode, staff_id: UUID) -> None:
    """Bring a retired record back (its name was kept reserved while retired). Refused under a retired parent."""
    if node.status != "RETIRED":
        raise TaxonomyError(409, "NOT_RETIRED", "Only a retired record can be restored.")
    parent = db.get(QuestionTaxonomyNode, node.parent_id) if node.parent_id else None
    if parent is not None and parent.status != "ACTIVE":
        raise TaxonomyError(409, "PARENT_RETIRED", f"Restore “{parent.name}” first.")
    node.status, node.updated_by, node.updated_at = "ACTIVE", staff_id, utcnow()
    db.flush()


# ------------------------------------------------------------------ questions

def link_question(db: Session, q: Question) -> None:
    """Point a question's chapter_id/topic_id/subtopic_id at the records its labels name (by name or alias) - its provider's list,
    or the platform list for a platform question - and write the records' canonical names into its labels. A label that matches
    nothing keeps its text and gets no link - nothing is created. The hierarchy is followed: a topic is looked up only under the
    matched chapter, a subtopic only under the matched topic."""
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


def in_use(db: Session, provider_id: UUID | None, subject_id: UUID, level: str, parent_id: UUID | None) -> bool:
    """Whether this place in the tree has records at all: a name typed here must then be one of them (a subject whose chapters are
    not set up yet still accepts typed names; they wait in the "Unlinked names" queue)."""
    return db.scalar(_place(select(QuestionTaxonomyNode.node_id), QuestionTaxonomyNode, provider_id, subject_id, parent_id)
                     .where(QuestionTaxonomyNode.level == level, QuestionTaxonomyNode.status == "ACTIVE").limit(1)) is not None


def unmatched(db: Session, q: Question) -> list[dict]:
    """After link_question: every label that names no record although its place has records - what a person must choose (the
    TAXONOMY_UNMATCHED problems of the Add question / Classify forms, with suggestions)."""
    out, parent_id = [], None
    for level in TAXONOMY_LEVELS:
        value, link = getattr(q, LABEL_COLUMN[level]), getattr(q, LINK_COLUMN[level])
        if value and link is None and (level == "CHAPTER" or parent_id) and in_use(db, q.provider_id, q.subject_id, level, parent_id):
            out.append({"level": level, "value": value, "parent_id": str(parent_id) if parent_id else None,
                        **suggestion_payload(suggest(db, q.provider_id, q.subject_id, level, parent_id, value))})
        parent_id = link
    return out


def suggestion_payload(result: dict) -> dict:
    """A suggest() result as JSON: suggestions [{node_id, name, score, band, matched}] best first, preselect node id, and elsewhere."""
    band = lambda score: "HIGH" if score >= taxonomy_match.STRONG else "MEDIUM"      # noqa: E731
    item = lambda n, score, name: {"node_id": str(n.node_id), "name": n.name, "score": score, "band": band(score),  # noqa: E731
                                   "matched": name, "parent_id": str(n.parent_id) if n.parent_id else None}
    return {"suggestions": [item(*c) for c in result["candidates"]],
            "preselect": str(result["preselect"].node_id) if result["preselect"] is not None else None,
            "elsewhere": [item(*c) for c in result["elsewhere"]]}


# ------------------------------------------------------------------ aliases, requests and merging (callers authorise first)

def remove_alias(db: Session, alias: QuestionTaxonomyAlias) -> None:
    """Withdraw an approved spelling (the questions it linked keep their link; future typing of it is no longer recognised)."""
    db.delete(alias)
    db.flush()


def request_alias(db: Session, node: QuestionTaxonomyNode, alias: str, staff_id: UUID, source: str,
                  source_ref: str | None = None) -> QuestionTaxonomyAliasRequest | None:
    """Ask the record's administrators to approve `alias`. None when it is already the record's name or one of its aliases; an
    identical pending request is returned instead of a second one. TaxonomyError when another record here uses that name."""
    if node.status != "ACTIVE":
        raise TaxonomyError(409, "RECORD_RETIRED", "Aliases can only be requested for an active record.")
    text, key = clean_name(node.level, alias)
    if key == node.name_key or db.scalar(select(QuestionTaxonomyAlias.alias_id).where(QuestionTaxonomyAlias.node_id == node.node_id,
                                                                                      QuestionTaxonomyAlias.alias_key == key)):
        return None
    _refuse_taken(db, node.provider_id, node.subject_id, node.level, node.parent_id, key)
    pending = db.scalar(select(QuestionTaxonomyAliasRequest).where(QuestionTaxonomyAliasRequest.node_id == node.node_id,
                                                                   QuestionTaxonomyAliasRequest.alias_key == key,
                                                                   QuestionTaxonomyAliasRequest.status == "PENDING"))
    if pending:
        return pending
    req = QuestionTaxonomyAliasRequest(node_id=node.node_id, provider_id=node.provider_id, subject_id=node.subject_id, alias=text,
                                       alias_key=key, source=source, source_ref=(source_ref or None) and str(source_ref)[:64],
                                       requested_by=staff_id)
    db.add(req)
    db.flush()
    return req


def decide_request(db: Session, req: QuestionTaxonomyAliasRequest, approve: bool, staff_id: UUID, ip: str | None,
                   reason: str | None = None) -> QuestionTaxonomyAlias | None:
    """Approve (the alias is created on the record - or on the record it was merged into) or reject a pending request."""
    if req.status != "PENDING":
        raise TaxonomyError(409, "ALREADY_DECIDED", "This request was already decided.")
    alias = None
    if approve:
        node = db.get(QuestionTaxonomyNode, req.node_id)
        while node is not None and node.status == "MERGED":
            node = db.get(QuestionTaxonomyNode, node.merged_into_id)
        alias = add_alias(db, node, req.alias, staff_id, ip)
    req.status, req.decided_by, req.decided_at = ("APPROVED" if approve else "REJECTED"), staff_id, utcnow()
    req.decision_reason = (reason or "").strip()[:500] or None
    db.flush()
    return alias


def merge(db: Session, node: QuestionTaxonomyNode, into: QuestionTaxonomyNode, staff_id: UUID, ip: str | None,
          _child: bool = False) -> dict:
    """Fold the duplicate `node` into `into` (same list, subject, level and parent): its questions are relinked and relabelled,
    its topics/subtopics move under `into` (a child with the same name is merged in turn), its aliases and pending requests move,
    its own name becomes an alias of `into`, and it is marked MERGED (never deleted). Returns what changed."""
    if node.node_id == into.node_id:
        raise TaxonomyError(422, "SAME_RECORD", "Choose another record to merge into.")
    same_parent = _child or node.parent_id == into.parent_id          # a child is merged into the same-named child of its new parent
    if not same_parent or (node.provider_id, node.subject_id, node.level) != (into.provider_id, into.subject_id, into.level):
        raise TaxonomyError(422, "NOT_SAME_PLACE", "Only records of the same subject, level and parent can be merged.")
    if node.status != "ACTIVE" or into.status != "ACTIVE":
        raise TaxonomyError(409, "RECORD_RETIRED", "Both records must be active.")
    moved = {"questions": 0, "children": 0, "aliases": 0}
    link, label = getattr(Question, LINK_COLUMN[node.level]), LABEL_COLUMN[node.level]
    moved["questions"] = db.execute(update(Question).where(link == node.node_id).values({link.key: into.node_id, label: into.name})
                                    .execution_options(synchronize_session=False)).rowcount or 0
    for child in db.scalars(select(QuestionTaxonomyNode).where(QuestionTaxonomyNode.parent_id == node.node_id,
                                                               QuestionTaxonomyNode.status == "ACTIVE")).all():
        same = db.scalar(select(QuestionTaxonomyNode).where(QuestionTaxonomyNode.parent_id == into.node_id,
                                                            QuestionTaxonomyNode.name_key == child.name_key))
        if same is not None and same.status == "ACTIVE":
            merge(db, child, same, staff_id, ip, _child=True)
        else:
            child.parent_id, child.updated_by, child.updated_at = into.node_id, staff_id, utcnow()
            db.execute(update(QuestionTaxonomyAlias).where(QuestionTaxonomyAlias.node_id == child.node_id)
                       .values(parent_id=into.node_id).execution_options(synchronize_session=False))
        moved["children"] += 1
    moved["aliases"] = db.execute(update(QuestionTaxonomyAlias).where(QuestionTaxonomyAlias.node_id == node.node_id)
                                  .values(node_id=into.node_id, parent_id=into.parent_id).execution_options(synchronize_session=False)).rowcount or 0
    db.execute(update(QuestionTaxonomyAliasRequest).where(QuestionTaxonomyAliasRequest.node_id == node.node_id,
                                                          QuestionTaxonomyAliasRequest.status == "PENDING")
               .values(node_id=into.node_id).execution_options(synchronize_session=False))
    node.status, node.merged_into_id, node.updated_by, node.updated_at = "MERGED", into.node_id, staff_id, utcnow()
    db.flush()
    db.add(QuestionTaxonomyAlias(node_id=into.node_id, provider_id=into.provider_id, subject_id=into.subject_id, parent_id=into.parent_id,
                                 alias=node.name, alias_key=node.name_key, approved_by=staff_id, approved_ip=ip))
    db.flush()
    return moved


# ------------------------------------------------------------------ the "Unlinked names" queue

def _question_owner(provider_id: UUID | None):
    return Question.provider_id == provider_id if provider_id else Question.scope == "GLOBAL"


def unlinked(db: Session, provider_id: UUID | None, subject_id: UUID, limit: int = 200) -> list[dict]:
    """Names typed on questions of one list and subject that are linked to no record, grouped by spelling (case/spacing ignored) and
    place: [{level, value, parent_id, questions, suggestions..., preselect}] - most used first."""
    groups: dict[tuple, dict] = {}
    for level in TAXONOMY_LEVELS:
        label, link = getattr(Question, LABEL_COLUMN[level]), getattr(Question, LINK_COLUMN[level])
        parent_link = getattr(Question, LINK_COLUMN[PARENT_LEVEL[level]]) if PARENT_LEVEL[level] else None
        stmt = select(label, parent_link if parent_link is not None else null(), func.count()) \
            .where(_question_owner(provider_id), Question.subject_id == subject_id, Question.status == "ACTIVE", label.is_not(None),
                   link.is_(None))
        if parent_link is not None:
            stmt = stmt.where(parent_link.is_not(None))           # a topic is resolved under its (linked) chapter
        stmt = stmt.group_by(label, parent_link) if parent_link is not None else stmt.group_by(label)
        for text, parent_id, count in db.execute(stmt):
            key = _key_or_none(text)
            if key is None:
                continue
            g = groups.setdefault((level, parent_id, key), {"level": level, "value": text, "parent_id": str(parent_id) if parent_id else None,
                                                             "questions": 0})
            g["questions"] += count
    order = {"CHAPTER": 0, "TOPIC": 1, "SUBTOPIC": 2}
    out = sorted(groups.values(), key=lambda g: (order[g["level"]], -g["questions"], g["value"].lower()))[:limit]
    for g in out:
        parent = UUID(g["parent_id"]) if g["parent_id"] else None
        g.update(suggestion_payload(suggest(db, provider_id, subject_id, g["level"], parent, g["value"])))
    return out


def resolve_unlinked(db: Session, provider_id: UUID | None, subject_id: UUID, level: str, parent_id: UUID | None, value: str,
                     node: QuestionTaxonomyNode) -> int:
    """Link every question of this list and subject whose `level` label is spelled like `value` (and sits under `parent_id`) to
    `node`, writing its canonical name. Returns how many questions changed."""
    key = _key_or_none(value)
    label, link = getattr(Question, LABEL_COLUMN[level]), getattr(Question, LINK_COLUMN[level])
    stmt = select(label).where(_question_owner(provider_id), Question.subject_id == subject_id, label.is_not(None), link.is_(None))
    if PARENT_LEVEL[level]:
        stmt = stmt.where(getattr(Question, LINK_COLUMN[PARENT_LEVEL[level]]) == parent_id)
    spellings = [t for (t,) in db.execute(stmt.distinct()) if _key_or_none(t) == key]
    if not spellings:
        return 0
    stmt = update(Question).where(_question_owner(provider_id), Question.subject_id == subject_id, label.in_(spellings), link.is_(None))
    if PARENT_LEVEL[level]:
        stmt = stmt.where(getattr(Question, LINK_COLUMN[PARENT_LEVEL[level]]) == parent_id)
    changed = db.execute(stmt.values({link.key: node.node_id, label.key: node.name}).execution_options(synchronize_session=False))
    db.flush()
    return changed.rowcount or 0
