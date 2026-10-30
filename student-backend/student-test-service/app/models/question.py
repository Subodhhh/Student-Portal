from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Question(Base):
    __tablename__ = "questions"

    __table_args__ = (
        UniqueConstraint(
            "test_id",
            "question_order",
            name="uq_questions_test_order",
        ),
        Index(
            "ix_questions_test_id",
            "test_id",
        ),
        Index(
            "ix_questions_test_subject",
            "test_id",
            "subject",
        ),
        Index(
            "ix_questions_test_paper",
            "test_id",
            "paper_id",
        ),
    )

    question_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    test_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tests.test_id", ondelete="CASCADE"),
        nullable=False,
    )

    subject: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Global Test with paper choice: the contribution (Institute submission id) this question belongs to; NULL for every other test.
    paper_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )

    question_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    question_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    question_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    option_a: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    option_b: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    option_c: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    option_d: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    correct_answer: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    marks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    negative_marks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    image_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )