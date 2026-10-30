from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.question import Question


def get_questions_by_test(
    db: Session,
    test_id: UUID,
    paper_ids=None,
) -> list[Question]:
    """paper_ids (Global Test with paper choice): only the questions of these papers; None = every question of the test."""
    query = select(Question).where(Question.test_id == test_id)
    if paper_ids is not None:
        query = query.where(Question.paper_id.in_(list(paper_ids)))
    query = (
        query
        .order_by(
            Question.subject.asc().nulls_last(),
            Question.question_order,
        )
    )

    return list(
        db.scalars(query).all()
    )