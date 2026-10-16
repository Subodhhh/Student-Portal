from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attempt_answer import (
    AttemptAnswer,
    QuestionState,
)


def get_answer(
    db: Session,
    attempt_id: UUID,
    question_id: UUID,
) -> AttemptAnswer | None:
    query = select(AttemptAnswer).where(
        AttemptAnswer.attempt_id == attempt_id,
        AttemptAnswer.question_id == question_id,
    )

    return db.scalars(query).first()


def create_answer(
    db: Session,
    answer: AttemptAnswer,
) -> AttemptAnswer:
    db.add(answer)
    db.commit()
    db.refresh(answer)

    return answer


def update_answer(
    db: Session,
    answer: AttemptAnswer,
    selected_answer: str | None,
    question_state: QuestionState,
) -> AttemptAnswer:
    now = datetime.now(timezone.utc)

    answer.selected_answer = selected_answer
    answer.question_state = question_state
    answer.updated_at = now

    if answer.visited_at is None:
        answer.visited_at = now

    if selected_answer is not None:
        answer.answered_at = now
    else:
        answer.answered_at = None

    db.commit()
    db.refresh(answer)

    return answer


def mark_question_visited(
    db: Session,
    answer: AttemptAnswer,
) -> AttemptAnswer:
    now = datetime.now(timezone.utc)

    if answer.visited_at is None:
        answer.visited_at = now

    if answer.question_state == QuestionState.NOT_VISITED:
        answer.question_state = QuestionState.VISITED_UNANSWERED

    answer.updated_at = now

    db.commit()
    db.refresh(answer)

    return answer


def get_answers_by_attempt(
    db: Session,
    attempt_id: UUID,
) -> list[AttemptAnswer]:
    query = (
        select(AttemptAnswer)
        .where(
            AttemptAnswer.attempt_id == attempt_id,
        )
        .order_by(AttemptAnswer.updated_at)
    )

    return list(db.scalars(query).all())