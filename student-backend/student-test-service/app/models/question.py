from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class QuestionImage(Base):
    """A picture shown in a question or an option, delivered by the Institute with the exam and named by its SHA-256 checksum, so
    the same picture is stored once however many tests use it. Served to a student only for a test the student may open
    (routes/test.py)."""
    __tablename__ = "question_images"

    checksum: Mapped[str] = mapped_column(String(64), primary_key=True)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    image_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


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

    marks: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        nullable=False,
    )

    negative_marks: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        nullable=False,
    )

    image_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # MSQ / NAT (app/question_types.py): `content` is shown to students (MSQ options, NAT number format); `answer_key` and `scheme`
    # are NEVER served before review. NULL for MCQ, whose options and key are the columns above (correct_answer holds "" for MSQ/NAT).
    content: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    answer_key: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    scheme: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # pictures (question_images.checksum) of the question and of each option; NULL: that field has no picture
    question_image: Mapped[str | None] = mapped_column(String(64), ForeignKey("question_images.checksum"), nullable=True)
    option_a_image: Mapped[str | None] = mapped_column(String(64), ForeignKey("question_images.checksum"), nullable=True)
    option_b_image: Mapped[str | None] = mapped_column(String(64), ForeignKey("question_images.checksum"), nullable=True)
    option_c_image: Mapped[str | None] = mapped_column(String(64), ForeignKey("question_images.checksum"), nullable=True)
    option_d_image: Mapped[str | None] = mapped_column(String(64), ForeignKey("question_images.checksum"), nullable=True)

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

class QuestionPicture(Base):
    """A picture referenced inside a question's `content` (MSQ option pictures, roles option_a .. option_h): lets the picture endpoint
    authorise it exactly like the picture columns of MCQ questions."""
    __tablename__ = "question_pictures"

    question_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("questions.question_id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), ForeignKey("question_images.checksum"), nullable=False, index=True)
