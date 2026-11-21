import uuid
from decimal import Decimal

from sqlalchemy import UUID, CheckConstraint, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TestResultQuestion(Base):
    """The marks awarded for one question of a result (decision D9): written with the result, replaced when an authorized
    re-evaluation changes it. Results evaluated before this table existed have no rows; their review keeps being computed with
    the same scorer (app/services/scoring.py), so the numbers are identical."""
    __tablename__ = "test_result_questions"
    __table_args__ = (CheckConstraint("status IN ('CORRECT','INCORRECT','UNANSWERED')", name="test_result_questions_status_check"),)

    result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("test_results.result_id", ondelete="CASCADE"),
                                                 primary_key=True)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    question_order: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    selected_answer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    marks_obtained: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
