from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.test_audience import TestAudience


def create_audience(
    db: Session,
    audience: TestAudience,
) -> TestAudience:
    db.add(audience)
    db.commit()
    db.refresh(audience)

    return audience


def get_audiences_by_test(
    db: Session,
    test_id: UUID,
) -> list[TestAudience]:
    query = select(TestAudience).where(
        TestAudience.test_id == test_id,
    )

    return list(db.scalars(query).all())