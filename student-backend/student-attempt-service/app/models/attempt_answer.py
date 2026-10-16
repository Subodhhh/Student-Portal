from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class QuestionState(str, Enum):
    NOT_VISITED = "NOT_VISITED"
    VISITED_UNANSWERED = "VISITED_UNANSWERED"
    ANSWERED = "ANSWERED"
    MARKED_FOR_REVIEW = "MARKED_FOR_REVIEW"


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_attempt_answers_attempt_question",
        ),
    )

    answer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "test_attempts.attempt_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    question_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    selected_answer: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )

    question_state: Mapped[QuestionState] = mapped_column(
        String(30),
        nullable=False,
        default=QuestionState.NOT_VISITED,
    )

    visited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )