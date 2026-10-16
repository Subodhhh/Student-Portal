from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.test_audience import TestAudience
from app.repositories.test_audience_repository import (
    create_audience,
    get_audiences_by_test,
)


def validate_audience(
    db: Session,
    audience_data,
) -> None:
    if audience_data.audience_type.value == "BRANCH":
        branch = db.execute(
            text(
                """
                SELECT branch_id
                FROM branches
                WHERE branch_id = :branch_id
                  AND provider_id = :provider_id
                """
            ),
            {
                "branch_id": audience_data.branch_id,
                "provider_id": audience_data.provider_id,
            },
        ).first()

        if not branch:
            raise HTTPException(
                status_code=400,
                detail="Branch does not belong to the specified provider",
            )

    elif audience_data.audience_type.value == "BATCH":
        batch = db.execute(
            text(
                """
                SELECT b.batch_id
                FROM batches b
                JOIN branches br
                    ON br.branch_id = b.branch_id
                WHERE b.batch_id = :batch_id
                  AND br.provider_id = :provider_id
                """
            ),
            {
                "batch_id": audience_data.batch_id,
                "provider_id": audience_data.provider_id,
            },
        ).first()

        if not batch:
            raise HTTPException(
                status_code=400,
                detail="Batch does not belong to the specified provider",
            )


def add_test_audience(
    db: Session,
    test_id: UUID,
    audience_data,
) -> TestAudience:

    validate_audience(
        db=db,
        audience_data=audience_data,
    )

    audience = TestAudience(
        test_id=test_id,
        audience_type=audience_data.audience_type,
        provider_id=audience_data.provider_id,
        branch_id=audience_data.branch_id,
        batch_id=audience_data.batch_id,
        student_id=audience_data.student_id,
    )

    return create_audience(
        db=db,
        audience=audience,
    )


def get_test_audiences(
    db: Session,
    test_id: UUID,
) -> list[TestAudience]:
    return get_audiences_by_test(
        db=db,
        test_id=test_id,
    )