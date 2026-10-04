from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attempt_answer import AttemptAnswer


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
) -> AttemptAnswer:
    answer.selected_answer = selected_answer
    db.commit()
    db.refresh(answer)

    return answer


def get_answers_by_attempt(
    db: Session,
    attempt_id: UUID,
) -> list[AttemptAnswer]:
    query = (
        select(AttemptAnswer)
        .where(AttemptAnswer.attempt_id == attempt_id)
        .order_by(AttemptAnswer.updated_at)
    )

    return list(db.scalars(query).all())