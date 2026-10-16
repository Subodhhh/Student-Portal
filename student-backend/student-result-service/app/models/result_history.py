import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ResultHistory(Base):
    """Immutable record of every change to a result: the full previous and new values, who authorized it and why.
    test_results always holds the CURRENT effective values; the first history row's `previous` is the original evaluation."""

    __tablename__ = "result_history"
    __table_args__ = (
        CheckConstraint("kind IN ('MARKS_ADJUSTMENT','REEVALUATION')", name="result_history_kind_check"),
        UniqueConstraint("correction_ref", "result_id", name="uq_result_history_ref_result"),
    )

    history_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("test_results.result_id"), nullable=False, index=True)
    test_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    correction_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    previous: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    new: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
