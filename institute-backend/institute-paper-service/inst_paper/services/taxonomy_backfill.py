"""Backfill the canonical taxonomy from the labels existing PROVIDER questions already carry.

For each provider and subject, the distinct chapter / topic / subtopic labels become records, following the hierarchy
(Chapter -> Topic under that chapter -> Subtopic under that topic):
  * labels that differ only in capitalisation, spacing or Unicode width are ONE record (core.taxonomy.label_key); its name is the
    spelling most questions use (ties: the spelling of the oldest question), and the report lists every variant so a reviewer can see what was merged;
  * a label that already matches a record (by name or approved alias) reuses it - nothing is duplicated, and re-running is safe;
  * a topic without a chapter (or a subtopic without a topic) cannot be placed in the hierarchy: it is reported, not created, and
    keeps its text label;
  * a label that is not a valid label (e.g. control characters) is reported and skipped.
Each question is then linked (chapter_id / topic_id / subtopic_id) and its labels set to the records' names. Question content and
versions are never touched. GLOBAL (platform) questions are not part of any provider's taxonomy and are left alone.

`dry_run=True` (the default everywhere) computes the full report and writes nothing.
"""
from collections import Counter, defaultdict
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from institute_common.core.taxonomy import LEVEL_MAX, clean_label, label_key
from institute_common.models.papers import Question

from inst_paper.services import taxonomy

PATH_LEVELS = (("CHAPTER", "chapter"), ("TOPIC", "topic"), ("SUBTOPIC", "subtopic"))


def _clean(level: str, value: str | None) -> tuple[str, str] | None:
    """(name, key) of a stored label, or None when it is blank or invalid."""
    try:
        name = clean_label(value, LEVEL_MAX[level])
    except ValueError:
        return None
    return (name, label_key(name)) if name else None


def _path(q: Question, report: dict) -> list[tuple[str, str, str]]:
    """The question's labels as a hierarchy path [(level, name, key), ...], stopping at the first gap. Gaps and invalid labels are
    counted in the report."""
    path = []
    for level, column in PATH_LEVELS:
        raw = getattr(q, column)
        if not raw:
            # a lower label below a missing one cannot be placed in the hierarchy
            if any(getattr(q, c) for _l, c in PATH_LEVELS[len(path) + 1:]):
                report["orphan_labels"] += 1
            break
        cleaned = _clean(level, raw)
        if cleaned is None:
            report["invalid_labels"].append({"question_id": str(q.question_id), "level": level, "value": raw[:100]})
            break
        path.append((level, *cleaned))
    return path


def backfill_subject(db: Session, provider_id: UUID, subject_id: UUID, *, staff_id: UUID, dry_run: bool = True) -> dict:
    """Backfill one provider's subject. Returns the report; writes (flushes, does not commit) only when dry_run is False."""
    report = {"provider_id": str(provider_id), "subject_id": str(subject_id), "questions": 0, "questions_to_link": 0,
              "records_existing": 0, "records_to_create": [], "merged_spellings": [], "orphan_labels": 0, "invalid_labels": []}
    questions = db.scalars(select(Question).where(Question.provider_id == provider_id, Question.subject_id == subject_id,
                                                  Question.scope == "PROVIDER").order_by(Question.created_at, Question.question_id)).all()
    report["questions"] = len(questions)
    paths = {q.question_id: _path(q, report) for q in questions}

    # every distinct place in the tree: (key path) -> how often each spelling is used
    spellings: dict[tuple, Counter] = defaultdict(Counter)
    for path in paths.values():
        for depth in range(len(path)):
            key_path = tuple(key for _level, _name, key in path[:depth + 1])
            spellings[key_path][path[depth][1]] += 1

    # resolve parents before children: shorter key paths first
    node_of: dict[tuple, object] = {}          # key path -> node (or a placeholder string in a dry run)
    for key_path in sorted(spellings, key=len):
        level = PATH_LEVELS[len(key_path) - 1][0]
        parent = node_of.get(key_path[:-1]) if len(key_path) > 1 else None
        counts = spellings[key_path]
        # most used spelling; on a tie max() keeps the first one met, i.e. the oldest question's (questions are read oldest first)
        name = max(counts.items(), key=lambda kv: kv[1])[0]
        if len(counts) > 1:
            report["merged_spellings"].append({"level": level, "name": name, "variants": sorted(counts)})
        existing = None
        if parent is None or not isinstance(parent, str):
            existing, _how = taxonomy.find(db, provider_id, subject_id, level, parent.node_id if parent else None, name)
        if existing:
            node_of[key_path] = existing
            report["records_existing"] += 1
            continue
        report["records_to_create"].append({"level": level, "name": name})
        if dry_run:
            node_of[key_path] = f"new:{'/'.join(key_path)}"
        else:
            node_of[key_path] = taxonomy.create_node(db, provider_id=provider_id, subject_id=subject_id, level=level,
                                                     parent_id=parent.node_id if parent else None, name=name, staff_id=staff_id, ip=None)

    for q in questions:
        if not paths[q.question_id]:
            continue
        report["questions_to_link"] += 1
        if not dry_run:
            taxonomy.link_question(db, q)
    if not dry_run:
        db.flush()
    return report


def backfill(db: Session, *, staff_id: UUID, provider_id: UUID | None = None, dry_run: bool = True) -> dict:
    """Backfill every (provider, subject) with labelled PROVIDER questions, optionally one provider only. Commits after each subject
    when not a dry run, so a large backfill keeps transactions small and a re-run continues where it stopped."""
    labelled = or_(Question.chapter.is_not(None), Question.topic.is_not(None), Question.subtopic.is_not(None))
    stmt = (select(Question.provider_id, Question.subject_id).where(Question.scope == "PROVIDER", labelled)
            .distinct().order_by(Question.provider_id, Question.subject_id))
    if provider_id:
        stmt = stmt.where(Question.provider_id == provider_id)
    subjects = []
    for prov, subj in db.execute(stmt).all():
        subjects.append(backfill_subject(db, prov, subj, staff_id=staff_id, dry_run=dry_run))
        if not dry_run:
            db.commit()
    return {"dry_run": dry_run, "subjects": subjects,
            "totals": {k: sum(len(s[k]) if isinstance(s[k], list) else s[k] for s in subjects)
                       for k in ("questions", "questions_to_link", "records_existing", "records_to_create", "orphan_labels", "invalid_labels")}}
