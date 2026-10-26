from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AttemptStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    AUTO_SUBMITTED = "AUTO_SUBMITTED"


class TestAttempt(Base):
    __tablename__ = "test_attempts"

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    test_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    student_id: Mapped[str] = mapped_column(
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    status: Mapped[AttemptStatus] = mapped_column(
        SQLEnum(
            AttemptStatus,
            name="attempt_status",
        ),
        nullable=False,
        default=AttemptStatus.IN_PROGRESS,
    )

    # Exam integrity (see the attempt_integrity_reviews migration): why the attempt was submitted, the review state of a warning
    # auto-submission, and the original attempt of an authorized retake. NULL for attempts made before.
    submit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)

    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    retake_of_attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("test_attempts.attempt_id"),
        nullable=True,
    )
