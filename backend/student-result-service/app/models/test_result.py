import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TestResult(Base):
    __tablename__ = "test_results"

    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        unique=True,
        nullable=False,
        index=True,
    )

    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    student_id: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_answers: Mapped[int] = mapped_column(Integer, nullable=False)
    incorrect_answers: Mapped[int] = mapped_column(Integer, nullable=False)
    unanswered_questions: Mapped[int] = mapped_column(Integer, nullable=False)

    total_marks: Mapped[int] = mapped_column(Integer, nullable=False)
    obtained_marks: Mapped[int] = mapped_column(Integer, nullable=False)

    accuracy: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )