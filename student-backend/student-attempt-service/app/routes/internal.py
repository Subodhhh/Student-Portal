"""Internal API for the Institute backend (no answers, no marks): attempt status per exam, and the exam-integrity record of an attempt
(warnings, automatic submission, review) with the provider's review decision.

Lets the Institute side mark each assignment STARTED / COMPLETED / MISSED from what students actually did, and lets authorized staff
investigate an automatic submission. The Institute side decides WHO may see or decide (its own provider/branch scoping); this API is
authenticated by X-Service-Key; 404 unless INSTITUTE_SERVICE_API_KEYS is configured."""
import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.integrity import AttemptReview, IntegrityEvent
from app.models.paper_selection import AttemptSelectedPaper
from app.models.test_attempt import TestAttempt
from app.services import integrity

MAX_ROWS = 20000


def require_service_key(x_service_key: str | None = Header(default=None)) -> None:
    keys = [k.strip() for k in settings.institute_service_api_keys.split(",") if k.strip()]
    if not keys:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_service_key or not any(hmac.compare_digest(x_service_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_service_key)])


@router.get("/tests/{test_id}/attempts")
def test_attempts(test_id: uuid.UUID, db: Session = Depends(get_db)):
    # oldest first: with an authorized retake a student has two attempts and the later one (the retake) is the current one
    rows = db.scalars(select(TestAttempt).where(TestAttempt.test_id == test_id).order_by(TestAttempt.started_at).limit(MAX_ROWS)).all()
    warnings = dict(db.execute(select(IntegrityEvent.attempt_id, func.count()).where(
        IntegrityEvent.attempt_id.in_([r.attempt_id for r in rows] or [None]), IntegrityEvent.counted.is_(True))
        .group_by(IntegrityEvent.attempt_id)).all())
    papers: dict = {}
    for a, subject, paper_id in db.execute(select(AttemptSelectedPaper.attempt_id, AttemptSelectedPaper.subject, AttemptSelectedPaper.paper_id)
                                           .where(AttemptSelectedPaper.attempt_id.in_([r.attempt_id for r in rows] or [None]))):
        papers.setdefault(a, {})[subject] = str(paper_id)
    return [
        {"student_id": r.student_id, "attempt_id": str(r.attempt_id), "status": r.status.value,
         "started_at": r.started_at.isoformat(), "expires_at": r.expires_at.isoformat(),
         "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
         "submit_reason": r.submit_reason, "review_status": r.review_status, "warnings": warnings.get(r.attempt_id, 0),
         "retake_of_attempt_id": str(r.retake_of_attempt_id) if r.retake_of_attempt_id else None,
         "selected_papers": papers.get(r.attempt_id, {})}      # Global Test with paper choice: subject -> paper ({} otherwise)
        for r in rows
    ]


@router.get("/attempts/{attempt_id}/integrity")
def attempt_integrity(attempt_id: uuid.UUID, db: Session = Depends(get_db)):
    """The full integrity record of one attempt: every reported event (counted or not), the automatic submission and the review."""
    attempt = db.get(TestAttempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    events = db.scalars(select(IntegrityEvent).where(IntegrityEvent.attempt_id == attempt_id).order_by(IntegrityEvent.received_at)).all()
    retake = db.scalar(select(TestAttempt).where(TestAttempt.retake_of_attempt_id == attempt_id))
    return {**integrity.state(db, attempt), "test_id": attempt.test_id, "student_id": attempt.student_id,
            "started_at": attempt.started_at, "submitted_at": attempt.submitted_at,
            "selected_papers": {x.subject: str(x.paper_id) for x in db.scalars(select(AttemptSelectedPaper).where(
                AttemptSelectedPaper.attempt_id == attempt_id))},
            "events": [{"kind": e.kind, "received_at": e.received_at, "client_occurred_at": e.client_occurred_at, "counted": e.counted,
                        "warning_no": e.warning_no} for e in events],
            "retake": {"attempt_id": retake.attempt_id, "status": retake.status.value, "started_at": retake.started_at,
                       "submitted_at": retake.submitted_at} if retake else None}


@router.get("/tests/{test_id}/reviews")
def test_reviews(test_id: uuid.UUID, db: Session = Depends(get_db)):
    """Review requests (and staff-opened reviews) of a test's attempts."""
    rows = db.execute(select(AttemptReview, TestAttempt.test_id).join(TestAttempt, TestAttempt.attempt_id == AttemptReview.attempt_id)
                      .where(TestAttempt.test_id == test_id).order_by(AttemptReview.requested_at)).all()
    return [{**integrity.review_out(r), "attempt_id": r.attempt_id, "student_id": r.student_id} for r, _t in rows]


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(RETAKE|UPHOLD)$")
    reason: str = Field(min_length=5, max_length=2000)
    staff_id: uuid.UUID
    staff_name: str = Field(min_length=1, max_length=255)


@router.post("/attempts/{attempt_id}/review-decision")
def review_decision(attempt_id: uuid.UUID, body: DecisionIn, db: Session = Depends(get_db)):
    return integrity.decide(db, attempt_id, body.decision, body.reason, body.staff_id, body.staff_name)
