from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.test_attempt import AttemptStatus, TestAttempt


def get_active_attempt(
    db: Session,
    student_id: str,
    test_id: UUID,
) -> TestAttempt | None:
    query = select(TestAttempt).where(
        TestAttempt.student_id == student_id,
        TestAttempt.test_id == test_id,
        TestAttempt.status == AttemptStatus.IN_PROGRESS,
    )

    return db.scalars(query).first()


def get_attempt_by_test(
    db: Session,
    student_id: str,
    test_id: UUID,
) -> TestAttempt | None:
    query = (
        select(TestAttempt)
        .where(
            TestAttempt.student_id == student_id,
            TestAttempt.test_id == test_id,
        )
        .order_by(TestAttempt.started_at.desc())
    )

    return db.scalars(query).first()


def get_completed_attempts(
    db: Session,
    student_id: str,
) -> list[TestAttempt]:
    query = (
        select(TestAttempt)
        .where(
            TestAttempt.student_id == student_id,
            TestAttempt.status.in_(
                [
                    AttemptStatus.SUBMITTED,
                    AttemptStatus.AUTO_SUBMITTED,
                ]
            ),
        )
        .order_by(TestAttempt.submitted_at.desc())
    )

    return db.scalars(query).all()


def get_attempt_by_id(
    db: Session,
    attempt_id: UUID,
    student_id: str,
) -> TestAttempt | None:
    query = select(TestAttempt).where(
        TestAttempt.attempt_id == attempt_id,
        TestAttempt.student_id == student_id,
    )

    return db.scalars(query).first()


def create_attempt(
    db: Session,
    attempt: TestAttempt,
) -> TestAttempt:
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return attempt