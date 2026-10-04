from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.attempt_answer import AttemptAnswer
from app.models.test_attempt import AttemptStatus
from app.repositories.attempt_answer_repository import (
    create_answer,
    get_answer,
    update_answer,
)
from app.repositories.attempt_repository import get_attempt_by_id


def save_answer(
    db: Session,
    attempt_id: UUID,
    student_id: str,
    question_id: UUID,
    selected_answer: str | None,
) -> AttemptAnswer:

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

    now = datetime.now(timezone.utc)

    expires_at = attempt.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if attempt.status == AttemptStatus.IN_PROGRESS and now >= expires_at:
        attempt.status = AttemptStatus.AUTO_SUBMITTED
        attempt.submitted_at = now
        db.commit()

    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail="Attempt is no longer active",
        )

    answer = get_answer(
        db=db,
        attempt_id=attempt_id,
        question_id=question_id,
    )

    if answer:
        return update_answer(
            db=db,
            answer=answer,
            selected_answer=selected_answer,
        )

    answer = AttemptAnswer(
        attempt_id=attempt_id,
        question_id=question_id,
        selected_answer=selected_answer,
        answered_at=now,
        updated_at=now,
    )

    return create_answer(
        db=db,
        answer=answer,
    )