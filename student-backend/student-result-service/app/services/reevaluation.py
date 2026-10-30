"""Re-scoring of already-evaluated attempts after an authorized answer-key correction.

`score` applies exactly the rules of result_service.evaluate_attempt (+marks right, -negative marks wrong, unanswered 0,
answers compared case/space-insensitively). Every change is written to the immutable result_history first-class record."""
import uuid

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.models.result_history import ResultHistory
from app.models.test_result import TestResult
from app.repositories.result_repository import get_questions_with_answers

MAX_RESULTS = 20000
SNAPSHOT_FIELDS = ("total_questions", "attempted_questions", "correct_answers", "incorrect_answers", "unanswered_questions",
                   "total_marks", "obtained_marks", "accuracy")


def score(questions) -> dict:
    total_questions = len(questions)
    attempted = correct = incorrect = total_marks = obtained = 0
    for q in questions:
        marks = q["marks"] or 0
        negative = q["negative_marks"] or 0
        selected, right = q["selected_answer"], q["correct_answer"]
        total_marks += marks
        if not selected:  # same test as result_service.evaluate_attempt
            continue
        attempted += 1
        if selected.strip().upper() == right.strip().upper():
            correct += 1
            obtained += marks
        else:
            incorrect += 1
            obtained -= negative
    return {"total_questions": total_questions, "attempted_questions": attempted, "correct_answers": correct,
            "incorrect_answers": incorrect, "unanswered_questions": total_questions - attempted, "total_marks": total_marks,
            "obtained_marks": obtained, "accuracy": round(correct / attempted * 100, 2) if attempted else 0.0}


def snapshot(result: TestResult) -> dict:
    return {f: getattr(result, f) for f in SNAPSHOT_FIELDS}


def apply(db: Session, result: TestResult, new: dict, *, kind: str, correction_ref: str, reason: str, actor: str) -> ResultHistory:
    """Write the history row (with the full previous values) and the new effective values together."""
    previous = snapshot(result)
    if "subject_adjustments" in new:            # Global Test attribution: kept with the history like the marks
        previous["subject_adjustments"] = result.subject_adjustments
    row = ResultHistory(result_id=result.result_id, test_id=result.test_id, student_id=result.student_id, kind=kind, correction_ref=correction_ref,
                        previous=previous, new={**previous, **new}, reason=reason, actor=actor)
    db.add(row)
    for k, v in new.items():
        setattr(result, k, v)
    return row


def reevaluate_test(db: Session, test_id: uuid.UUID, *, correction_ref: str, reason: str, actor: str, paper_ids=None) -> dict:
    out = {"changed": [], "unchanged": 0, "skipped_manual": [], "already_applied": 0, "no_questions": 0}
    results = db.scalars(select(TestResult).where(TestResult.test_id == test_id).order_by(TestResult.student_id).limit(MAX_RESULTS)).all()
    if paper_ids is not None:
        # a corrected paper re-scores only the attempts that sat it; nobody else's result is touched or given a history row. An
        # attempt without a snapshot sat every paper of its test (a test made for one combination of papers), so it is included.
        snapped = text("SELECT attempt_id FROM attempt_selected_papers WHERE attempt_id IN :a").bindparams(bindparam("a", expanding=True))
        with_snapshot = {str(a) for a in db.scalars(snapped, {"a": [r.attempt_id for r in results] or [None]})}
        sat = {str(a) for a in db.scalars(text("SELECT attempt_id FROM attempt_selected_papers WHERE paper_id IN :p").bindparams(
            bindparam("p", expanding=True)), {"p": list(paper_ids)})}
        affected = [r for r in results if str(r.attempt_id) in sat or str(r.attempt_id) not in with_snapshot]
        out["not_affected"] = len(results) - len(affected)
        results = affected
    for r in results:
        done = db.scalar(select(ResultHistory.history_id).where(ResultHistory.result_id == r.result_id, ResultHistory.correction_ref == correction_ref))
        if done:
            out["already_applied"] += 1
            continue
        manual = db.scalar(select(ResultHistory.history_id).where(ResultHistory.result_id == r.result_id, ResultHistory.kind == "MARKS_ADJUSTMENT"))
        if manual:
            # A person set these marks deliberately; re-scoring must not silently overwrite them.
            out["skipped_manual"].append(r.student_id)
            continue
        questions = get_questions_with_answers(db, r.attempt_id)
        if not questions:
            out["no_questions"] += 1
            continue
        new = score(questions)
        if new == snapshot(r):
            out["unchanged"] += 1
            continue
        apply(db, r, new, kind="REEVALUATION", correction_ref=correction_ref, reason=reason, actor=actor)
        out["changed"].append(r.student_id)
    db.commit()
    return out
