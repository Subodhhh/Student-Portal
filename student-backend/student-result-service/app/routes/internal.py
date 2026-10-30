"""Read-only internal API for the Institute backend (results of one exam).

The Institute backend decides WHO may see an exam's results (provider/branch/subject scope)
and calls this with a service key; this service only returns the stored, already-evaluated
rows. No writes, no answer content."""
import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Uuid, bindparam, select, text
from sqlalchemy.orm import Session

from app.services.official import official
from app.config import settings
from app.database import get_db
from app.models.result_history import ResultHistory
from app.models.test_result import TestResult
from app.repositories.result_repository import get_questions_with_answers
from app.services import global_results, reevaluation

MAX_ROWS = 5000


def require_service_key(x_service_key: str | None = Header(default=None)) -> None:
    keys = [k.strip() for k in settings.institute_service_api_keys.split(",") if k.strip()]
    if not keys:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_service_key or not any(hmac.compare_digest(x_service_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_service_key)])


@router.get("/tests/{test_id}/results")
def exam_results(test_id: uuid.UUID, db: Session = Depends(get_db)):
    # official results only: one per student (an authorized retake replaces its original once evaluated)
    rows = db.scalars(select(TestResult).where(TestResult.test_id == test_id, official).order_by(TestResult.obtained_marks.desc()).limit(MAX_ROWS)).all()
    return [
        {"student_id": r.student_id, "total_marks": r.total_marks, "obtained_marks": r.obtained_marks,
         "attempted_questions": r.attempted_questions, "correct_answers": r.correct_answers,
         "incorrect_answers": r.incorrect_answers, "unanswered_questions": r.unanswered_questions,
         "accuracy": r.accuracy, "submitted_at": r.created_at.isoformat()}
        for r in rows
    ]


@router.get("/tests/{test_id}/results/subjects")
def exam_subject_results(test_id: uuid.UUID, db: Session = Depends(get_db)):
    """Per student, every subject's marks, scored with exactly the evaluation rules (reevaluation.score) from the stored answers and
    the current answer key. For readers allowed only some subjects (a provider's view of a unified Global Test exam): the Institute
    backend keeps only their subjects and never shows the overall score to them. A Global Test's subjects carry their paper (when
    the student chose papers) and their attributed manual adjustment; an unattributed one is reported on its own."""
    out = []
    global_test = global_results.is_global_test(db, test_id)
    # shortcut: one answers query per evaluated attempt; batch it if exams reach thousands of students.
    for r in db.scalars(select(TestResult).where(TestResult.test_id == test_id, official).order_by(TestResult.student_id).limit(MAX_ROWS)):
        by_subject: dict[str, list] = {}
        for q in get_questions_with_answers(db=db, attempt_id=r.attempt_id):
            by_subject.setdefault(q["subject"] or "Other", []).append(q)
        subjects = [{"subject": name, **reevaluation.score(qs)} for name, qs in by_subject.items()]
        row = {"student_id": r.student_id, "submitted_at": r.created_at.isoformat(), "subjects": subjects}
        if global_test:
            # each subject's paper (None without paper choice) and attributed adjustment (included in its obtained_marks); an
            # unattributed adjustment is reported on its own, never spread over subjects
            snap, deltas = global_results.snapshot(db, r.attempt_id), global_results.deltas(db, r.result_id, r.subject_adjustments)
            for x in subjects:
                x["paper_id"], x["adjustment"] = snap.get(x["subject"]), (deltas or {}).get(x["subject"], 0)
                x["obtained_marks"] += x["adjustment"]
            row["unattributed_adjustment"] = r.obtained_marks - sum(x["obtained_marks"] for x in subjects) if deltas is None else None
        out.append(row)
    return out


def _sections(db: Session, test_id):
    return db.execute(text("SELECT sections FROM tests WHERE test_id = :t").bindparams(bindparam("t", type_=Uuid)), {"t": test_id}).scalar()


class SubjectMarksIn(BaseModel):
    subject: str = Field(min_length=1, max_length=100)
    obtained_marks: int


class AdjustIn(BaseModel):
    test_id: uuid.UUID
    student_id: str = Field(min_length=1, max_length=20)
    obtained_marks: int
    reason: str = Field(min_length=5, max_length=1000)
    actor: str = Field(min_length=1, max_length=100)
    correction_ref: str = Field(min_length=8, max_length=100)
    # a Global Test only: the marks per subject of the attempt (each once, adding up to obtained_marks)
    subjects: list[SubjectMarksIn] | None = Field(default=None, max_length=100)


class ReevaluateIn(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)
    actor: str = Field(min_length=1, max_length=100)
    correction_ref: str = Field(min_length=8, max_length=100)
    # a Global Test: only results whose attempt sat one of these papers (None = every result of the test)
    paper_ids: list[uuid.UUID] | None = Field(default=None, max_length=1000)


@router.post("/results/adjust")
def adjust_marks(body: AdjustIn, db: Session = Depends(get_db)):
    """Sets a student's effective marks after an authorized manual review. The original and every earlier value stay in
    result_history; replaying the same correction_ref changes nothing."""
    rows = db.scalars(select(TestResult).where(TestResult.test_id == body.test_id, TestResult.student_id == body.student_id, official)).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Result not found")
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail="More than one result for this student and test")
    result = rows[0]
    done = db.scalar(select(ResultHistory).where(ResultHistory.result_id == result.result_id, ResultHistory.correction_ref == body.correction_ref))
    if done:
        return {"result_id": str(result.result_id), "previous": done.previous, "new": done.new, "replayed": True}
    if abs(body.obtained_marks) > result.total_marks:
        raise HTTPException(status_code=422, detail="Marks must lie between -total and +total")
    new = {"obtained_marks": body.obtained_marks}
    if global_results.is_global_test(db, result.test_id):
        new["subject_adjustments"] = _attribution(db, result, body)
    elif body.subjects is not None:
        raise HTTPException(status_code=422, detail="Subject marks apply only to Global Tests")
    if body.obtained_marks == result.obtained_marks and new.get("subject_adjustments", result.subject_adjustments) == result.subject_adjustments:
        raise HTTPException(status_code=409, detail="Marks are already that value")
    row = reevaluation.apply(db, result, new, kind="MARKS_ADJUSTMENT", correction_ref=body.correction_ref,
                             reason=body.reason, actor=body.actor)
    db.commit()
    return {"result_id": str(result.result_id), "previous": row.previous, "new": row.new, "replayed": False}


def _attribution(db: Session, result: TestResult, body: AdjustIn) -> dict | None:
    """A Global Test result's {subject: delta} for the new marks: from the given subject marks; the whole delta for a single-subject attempt;
    otherwise None - an unattributed adjustment, never guessed (the result leaves subject rankings until re-attributed)."""
    by_subject: dict[str, list] = {}
    for q in get_questions_with_answers(db=db, attempt_id=result.attempt_id):
        by_subject.setdefault(q["subject"] or "Other", []).append(q)
    scored = {name: reevaluation.score(qs) for name, qs in by_subject.items()}
    if body.subjects is None:
        if len(scored) != 1:
            return None
        (name, sc), = scored.items()
        return {name: body.obtained_marks - sc["obtained_marks"]} if body.obtained_marks != sc["obtained_marks"] else {}
    targets = {x.subject: x.obtained_marks for x in body.subjects}
    if len(targets) != len(body.subjects) or set(targets) != set(scored):
        raise HTTPException(status_code=422, detail="Give marks for every subject of the attempt exactly once")
    if any(abs(t) > scored[name]["total_marks"] for name, t in targets.items()):
        raise HTTPException(status_code=422, detail="A subject's marks must lie between -its total and +its total")
    if sum(targets.values()) != body.obtained_marks:
        raise HTTPException(status_code=422, detail="Subject marks must add up to obtained_marks")
    return {name: t - scored[name]["obtained_marks"] for name, t in targets.items() if t != scored[name]["obtained_marks"]}


@router.post("/tests/{test_id}/reevaluate")
def reevaluate(test_id: uuid.UUID, body: ReevaluateIn, db: Session = Depends(get_db)):
    """Re-scores every result of a test against the CURRENT answer key (after a key correction). Results a person adjusted by
    hand are skipped and reported. Idempotent per correction_ref. paper_ids (a Global Test): only results whose attempt sat one of them."""
    return reevaluation.reevaluate_test(db, test_id, correction_ref=body.correction_ref, reason=body.reason, actor=body.actor,
                                        paper_ids=body.paper_ids)


@router.get("/tests/{test_id}/results/history")
def result_history(test_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = db.scalars(select(ResultHistory).where(ResultHistory.test_id == test_id).order_by(ResultHistory.created_at).limit(5000)).all()
    return [{"student_id": r.student_id, "kind": r.kind, "previous": r.previous, "new": r.new, "reason": r.reason, "actor": r.actor,
             "correction_ref": r.correction_ref, "at": r.created_at.isoformat()} for r in rows]
