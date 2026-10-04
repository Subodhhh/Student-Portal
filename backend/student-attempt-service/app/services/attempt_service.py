from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.test_attempt import AttemptStatus, TestAttempt
from app.repositories.attempt_repository import (
    create_attempt,
    get_active_attempt,
    get_attempt_by_id,
)


def start_attempt(
    db: Session,
    student_id: str,
    test_id: UUID,
    duration_minutes: int,
):
    existing_attempt = get_active_attempt(
        db=db,
        student_id=student_id,
        test_id=test_id,
    )

    if existing_attempt:
        now = datetime.now(timezone.utc)

        expires_at = existing_attempt.expires_at

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now >= expires_at:
            existing_attempt.status = AttemptStatus.AUTO_SUBMITTED
            existing_attempt.submitted_at = now

            db.commit()
            db.refresh(existing_attempt)

        return existing_attempt

    started_at = datetime.now(timezone.utc)
    expires_at = started_at + timedelta(minutes=duration_minutes)

    attempt = TestAttempt(
        test_id=test_id,
        student_id=student_id,
        started_at=started_at,
        expires_at=expires_at,
        status=AttemptStatus.IN_PROGRESS,
    )

    return create_attempt(
        db=db,
        attempt=attempt,
    )


def get_attempt(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    attempt = get_attempt_by_id(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not attempt:
        return None

    now = datetime.now(timezone.utc)

    expires_at = attempt.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if (
        attempt.status == AttemptStatus.IN_PROGRESS
        and now >= expires_at
    ):
        attempt.status = AttemptStatus.AUTO_SUBMITTED
        attempt.submitted_at = now

        db.commit()
        db.refresh(attempt)

    return attempt


def submit_attempt(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    attempt = get_attempt_by_id(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Attempt not found",
        )

    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail="Attempt is no longer active",
        )

    now = datetime.now(timezone.utc)

    expires_at = attempt.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now >= expires_at:
        attempt.status = AttemptStatus.AUTO_SUBMITTED
    else:
        attempt.status = AttemptStatus.SUBMITTED

    attempt.submitted_at = now

    db.commit()
    db.refresh(attempt)

    return attempt