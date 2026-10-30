"""Global Test results. With paper choice (one test per occurrence, several papers per subject) an attempt is scored only on the
papers of its immutable snapshot (attempt-service attempt_selected_papers) and its subjects are the student's own (test-service
student_paper_eligibility); both are read here, never written. An attempt without a snapshot sat every question of its test.

Manual adjustments of any Global Test result: test_results.subject_adjustments = {subject: delta}, already included in obtained_marks
(never added again). A per-subject view is the scored marks plus that subject's delta. An adjustment without a subject attribution (NULL with a
MARKS_ADJUSTMENT on record) is never guessed: it is shown as unattributed, and the result leaves subject rankings until re-attributed."""
import json

from sqlalchemy import Uuid, bindparam, select, text
from sqlalchemy.orm import Session

from app.models.result_history import ResultHistory

# The questions an attempt is scored on (needs :attempt_id and a questions alias q): its snapshot's papers, or all without a snapshot.
SNAPSHOT_FILTER = """(NOT EXISTS (SELECT 1 FROM attempt_selected_papers sp WHERE sp.attempt_id = :attempt_id)
                      OR q.paper_id IN (SELECT sp.paper_id FROM attempt_selected_papers sp WHERE sp.attempt_id = :attempt_id))"""


def is_global_test(db: Session, test_id) -> bool:
    return db.execute(text("SELECT global_occurrence_id FROM tests WHERE test_id = :t").bindparams(bindparam("t", type_=Uuid)),
                      {"t": test_id}).scalar() is not None


def has_paper_choice(sections) -> bool:
    if isinstance(sections, str):
        sections = json.loads(sections)
    return bool(sections) and all("papers" in s for s in sections)


def snapshot(db: Session, attempt_id) -> dict[str, str]:
    return {s: str(p) for s, p in db.execute(text("SELECT subject, paper_id FROM attempt_selected_papers WHERE attempt_id = :a"),
                                              {"a": attempt_id})}


def student_sections(db: Session, sections, test_id, student_id: str, snap: dict[str, str]) -> list[dict]:
    """The student's sections after their attempt: a subject of the snapshot is PAPER; any other keeps its eligibility status and
    message (why there was no paper)."""
    rows = db.execute(text("SELECT subject, status, message FROM student_paper_eligibility WHERE test_id = :t AND student_id = :s"),
                      {"t": test_id, "s": student_id}).all()
    reason = {subject: (status, message) for subject, status, message in rows}
    out = []
    for s in sorted(sections, key=lambda s: s["position"]):
        status, message = ("PAPER", None) if s["subject"] in snap else reason.get(s["subject"], (None, None))
        if status is None:
            continue                    # cannot happen: delivery gives every student one cell per subject
        out.append({"position": s["position"], "subject": s["subject"], "status": status, "message": message})
    return out


def has_manual(db: Session, result_id) -> bool:
    return db.scalar(select(ResultHistory.history_id).where(ResultHistory.result_id == result_id,
                                                            ResultHistory.kind == "MARKS_ADJUSTMENT").limit(1)) is not None


def deltas(db: Session, result_id, subject_adjustments) -> dict[str, int] | None:
    """{subject: delta} of a Global Test result ({} when never adjusted); None when its adjustment is unattributed."""
    if isinstance(subject_adjustments, str):
        subject_adjustments = json.loads(subject_adjustments)
    if subject_adjustments is not None:
        return {k: int(v) for k, v in subject_adjustments.items()}
    return None if has_manual(db, result_id) else {}
