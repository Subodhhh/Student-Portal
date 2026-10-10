from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.question import Question


def get_questions_by_test(
    db: Session,
    test_id: UUID,
) -> list[Question]:
    query = (
        select(Question)
        .where(Question.test_id == test_id)
        .order_by(
            Question.subject.asc().nulls_last(),
            Question.question_order,
        )
    )

    return list(
        db.scalars(query).all()
    )