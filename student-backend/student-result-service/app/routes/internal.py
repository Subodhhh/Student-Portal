"""Read-only internal API for the Institute backend (results of one exam).

The Institute backend decides WHO may see an exam's results (provider/branch/subject scope)
and calls this with a service key; this service only returns the stored, already-evaluated
rows. No writes, no answer content."""
import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.result_history import ResultHistory
from app.models.test_result import TestResult
from app.repositories.result_repository import get_questions_with_answers
from app.services import reevaluation

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
    rows = db.scalars(select(TestResult).where(TestResult.test_id == test_id).order_by(TestResult.obtained_marks.desc()).limit(MAX_ROWS)).all()
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
    backend keeps only their subjects and never shows the overall score to them. A manual marks adjustment changes only the overall
    result, so it is not part of any subject here."""
    out = []
    # shortcut: one answers query per evaluated attempt; batch it if exams reach thousands of students.
    for r in db.scalars(select(TestResult).where(TestResult.test_id == test_id).order_by(TestResult.student_id).limit(MAX_ROWS)):
        by_subject: dict[str, list] = {}
        for q in get_questions_with_answers(db=db, attempt_id=r.attempt_id):
            by_subject.setdefault(q["subject"] or "Other", []).append(q)
        out.append({"student_id": r.student_id, "submitted_at": r.created_at.isoformat(),
                    "subjects": [{"subject": name, **reevaluation.score(qs)} for name, qs in by_subject.items()]})
    return out


class AdjustIn(BaseModel):
    test_id: uuid.UUID
    student_id: str = Field(min_length=1, max_length=20)
    obtained_marks: int
    reason: str = Field(min_length=5, max_length=1000)
    actor: str = Field(min_length=1, max_length=100)
    correction_ref: str = Field(min_length=8, max_length=100)


class ReevaluateIn(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)
    actor: str = Field(min_length=1, max_length=100)
    correction_ref: str = Field(min_length=8, max_length=100)


@router.post("/results/adjust")
def adjust_marks(body: AdjustIn, db: Session = Depends(get_db)):
    """Sets a student's effective marks after an authorized manual review. The original and every earlier value stay in
    result_history; replaying the same correction_ref changes nothing."""
    rows = db.scalars(select(TestResult).where(TestResult.test_id == body.test_id, TestResult.student_id == body.student_id)).all()
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
    if body.obtained_marks == result.obtained_marks:
        raise HTTPException(status_code=409, detail="Marks are already that value")
    row = reevaluation.apply(db, result, {"obtained_marks": body.obtained_marks}, kind="MARKS_ADJUSTMENT", correction_ref=body.correction_ref,
                             reason=body.reason, actor=body.actor)
    db.commit()
    return {"result_id": str(result.result_id), "previous": row.previous, "new": row.new, "replayed": False}


@router.post("/tests/{test_id}/reevaluate")
def reevaluate(test_id: uuid.UUID, body: ReevaluateIn, db: Session = Depends(get_db)):
    """Re-scores every result of a test against the CURRENT answer key (after a key correction). Results a person adjusted by
    hand are skipped and reported. Idempotent per correction_ref."""
    return reevaluation.reevaluate_test(db, test_id, correction_ref=body.correction_ref, reason=body.reason, actor=body.actor)


@router.get("/tests/{test_id}/results/history")
def result_history(test_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = db.scalars(select(ResultHistory).where(ResultHistory.test_id == test_id).order_by(ResultHistory.created_at).limit(5000)).all()
    return [{"student_id": r.student_id, "kind": r.kind, "previous": r.previous, "new": r.new, "reason": r.reason, "actor": r.actor,
             "correction_ref": r.correction_ref, "at": r.created_at.isoformat()} for r in rows]
