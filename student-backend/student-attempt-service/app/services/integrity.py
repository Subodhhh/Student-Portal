"""Exam integrity: browser warnings counted ON THE SERVER, automatic submission at the configured threshold, review requests and
provider-authorized retakes.

A browser cannot prevent leaving the exam; it can report it. The browser reports events (tab hidden, window blur, fullscreen exit);
the server decides what counts, so a modified client cannot lower the count:
  * an event is recorded once per client_event_id (a retried report never counts twice);
  * events within `integrity_debounce_seconds` of the last counted one are one action (switching tabs fires both blur and
    visibilitychange): recorded, not counted;
  * an event of an attempt that is no longer running is not recorded (refresh / resume / network retries after submission);
  * on reaching `integrity_warning_threshold` counted warnings the attempt is submitted as AUTO_SUBMITTED with submit_reason
    WARNING_THRESHOLD and review_status UNDER_REVIEW; the result service evaluates it exactly once like any submitted attempt (its
    unique attempt_id), with the saved answers and the test's marking.
Events are evidence of activity in the browser, not proof of intent: they are stored as reported, with no accusation.

Retakes: only the internal (Institute, service-key) API authorizes one, for a warning auto-submission; the student then starts a NEW
attempt linked to the original (retake_of_attempt_id). The original attempt, its answers, events and result are never changed. The
original stays official until the retake is submitted; then the retake is official (the result/ranking queries apply this)."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.integrity import EVENT_KINDS, AttemptReview, IntegrityEvent
from app.models.test_attempt import AttemptStatus, TestAttempt


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def warnings_of(db: Session, attempt_id: UUID) -> int:
    return db.scalar(select(func.count()).select_from(IntegrityEvent).where(IntegrityEvent.attempt_id == attempt_id,
                                                                           IntegrityEvent.counted.is_(True))) or 0


def state(db: Session, attempt: TestAttempt, **extra) -> dict:
    review = db.scalar(select(AttemptReview).where(AttemptReview.attempt_id == attempt.attempt_id))
    return {"attempt_id": attempt.attempt_id, "status": attempt.status.value, "warnings": warnings_of(db, attempt.attempt_id),
            "threshold": settings.integrity_warning_threshold, "submit_reason": attempt.submit_reason,
            "review_status": attempt.review_status, "retake_of_attempt_id": attempt.retake_of_attempt_id,
            "review": review_out(review) if review else None, **extra}


def review_out(r: AttemptReview) -> dict:
    return {"review_id": r.review_id, "status": r.status, "student_message": r.student_message, "requested_at": r.requested_at,
            "decided_at": r.decided_at, "decided_by_name": r.decided_by_name, "decision_reason": r.decision_reason,
            "retake_attempt_id": r.retake_attempt_id}


def finish(attempt: TestAttempt, now: datetime, reason: str) -> None:
    """Finalize a running attempt (the single place that sets the submission reason)."""
    attempt.status = AttemptStatus.SUBMITTED if reason == "STUDENT" else AttemptStatus.AUTO_SUBMITTED
    attempt.submit_reason = reason
    attempt.submitted_at = now
    if reason == "WARNING_THRESHOLD":
        attempt.review_status = "UNDER_REVIEW"


def record_event(db: Session, attempt_id: UUID, student_id: str, client_event_id: UUID, kind: str,
                 client_occurred_at: datetime | None) -> dict:
    if kind not in EVENT_KINDS:
        raise HTTPException(status_code=422, detail="Unknown event kind")
    attempt = db.scalar(select(TestAttempt).where(TestAttempt.attempt_id == attempt_id, TestAttempt.student_id == student_id)
                        .with_for_update())                # one report at a time per attempt: counts and warning numbers never race
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    seen = db.scalar(select(IntegrityEvent).where(IntegrityEvent.attempt_id == attempt_id, IntegrityEvent.client_event_id == client_event_id))
    if seen:
        db.rollback()
        return state(db, attempt, recorded=False, counted=seen.counted, warning_no=seen.warning_no, auto_submitted=False)
    now = datetime.now(timezone.utc)
    if attempt.status == AttemptStatus.IN_PROGRESS and now >= _utc(attempt.expires_at):
        finish(attempt, now, "TIME_EXPIRED")
        db.commit()
    if attempt.status != AttemptStatus.IN_PROGRESS:
        db.rollback()
        return state(db, attempt, recorded=False, counted=False, warning_no=None, auto_submitted=False)

    last = db.scalar(select(func.max(IntegrityEvent.received_at)).where(IntegrityEvent.attempt_id == attempt_id, IntegrityEvent.counted.is_(True)))
    counted = last is None or (now - _utc(last)).total_seconds() >= settings.integrity_debounce_seconds
    warning_no = warnings_of(db, attempt_id) + 1 if counted else None
    db.add(IntegrityEvent(attempt_id=attempt_id, client_event_id=client_event_id, kind=kind, client_occurred_at=client_occurred_at,
                          received_at=now, counted=counted, warning_no=warning_no))
    auto = bool(counted and warning_no >= settings.integrity_warning_threshold)
    if auto:
        finish(attempt, now, "WARNING_THRESHOLD")
    try:
        db.commit()
    except IntegrityError:          # the same report arrived twice at once: the first one won
        db.rollback()
        return state(db, attempt, recorded=False, counted=False, warning_no=None, auto_submitted=False)
    db.refresh(attempt)
    return state(db, attempt, recorded=True, counted=counted, warning_no=warning_no, auto_submitted=auto)


def request_review(db: Session, attempt_id: UUID, student_id: str, message: str) -> dict:
    attempt = db.scalar(select(TestAttempt).where(TestAttempt.attempt_id == attempt_id, TestAttempt.student_id == student_id))
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    if attempt.submit_reason != "WARNING_THRESHOLD":
        raise HTTPException(status_code=400, detail="Only an attempt submitted automatically after warnings can be reviewed")
    existing = db.scalar(select(AttemptReview).where(AttemptReview.attempt_id == attempt_id))
    if existing:                    # one request per attempt; asking again returns it unchanged
        return review_out(existing)
    r = AttemptReview(attempt_id=attempt_id, student_id=student_id, student_message=message.strip())
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        r = db.scalar(select(AttemptReview).where(AttemptReview.attempt_id == attempt_id))
    return review_out(r)


def decide(db: Session, attempt_id: UUID, decision: str, reason: str, staff_id: UUID, staff_name: str) -> dict:
    """RETAKE authorizes one new attempt for this student and test; UPHOLD keeps the automatic submission. Decided once: repeating the
    same decision returns it; a different one is refused."""
    attempt = db.scalar(select(TestAttempt).where(TestAttempt.attempt_id == attempt_id).with_for_update())
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    if attempt.submit_reason != "WARNING_THRESHOLD":
        raise HTTPException(status_code=409, detail="Only an attempt submitted automatically after warnings can be reviewed")
    status = {"RETAKE": "RETAKE_AUTHORIZED", "UPHOLD": "UPHELD"}[decision]
    review = db.scalar(select(AttemptReview).where(AttemptReview.attempt_id == attempt_id))
    if review and review.status != "OPEN":
        if review.status == status:
            db.rollback()
            return review_out(review)
        raise HTTPException(status_code=409, detail=f"This review was already decided: {review.status}")
    if review is None:              # staff may investigate without a student request
        review = AttemptReview(attempt_id=attempt_id, student_id=attempt.student_id, student_message="(opened by staff)")
        db.add(review)
    review.status, review.decision_reason = status, reason.strip()
    review.decided_by_staff_id, review.decided_by_name, review.decided_at = staff_id, staff_name, datetime.now(timezone.utc)
    attempt.review_status = status
    db.commit()
    return review_out(review)


def start_retake(db: Session, original: TestAttempt, duration_minutes: int) -> TestAttempt | None:
    """The authorized retake of `original`, created on first start (its own full duration), or None when none is authorized."""
    review = db.scalar(select(AttemptReview).where(AttemptReview.attempt_id == original.attempt_id))
    if not review or review.status != "RETAKE_AUTHORIZED" or review.retake_attempt_id:
        return None
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    retake = TestAttempt(test_id=original.test_id, student_id=original.student_id, started_at=now, expires_at=now + timedelta(minutes=duration_minutes),
                         status=AttemptStatus.IN_PROGRESS, retake_of_attempt_id=original.attempt_id)
    db.add(retake)
    db.flush()
    from app.services.paper_selection import copy_snapshot
    copy_snapshot(db, original.attempt_id, retake.attempt_id)     # the same papers, committed with the retake
    review.retake_attempt_id = retake.attempt_id
    try:
        db.commit()
    except IntegrityError:          # started twice at once: the other request created it
        db.rollback()
        return db.scalar(select(TestAttempt).where(TestAttempt.retake_of_attempt_id == original.attempt_id))
    db.refresh(retake)
    return retake
