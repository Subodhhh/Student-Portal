"""Exam integrity records (see the attempt_integrity_reviews migration). Append-only by convention: rows are inserted, and a review is
decided once; nothing here changes an attempt's answers or result."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

EVENT_KINDS = ("TAB_HIDDEN", "WINDOW_BLUR", "FULLSCREEN_EXIT")


def _now():
    return datetime.now(timezone.utc)


class IntegrityEvent(Base):
    __tablename__ = "attempt_integrity_events"
    __table_args__ = (UniqueConstraint("attempt_id", "client_event_id", name="uq_integrity_events_client_event"),
                      UniqueConstraint("attempt_id", "warning_no", name="uq_integrity_events_warning_no"))

    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("test_attempts.attempt_id"), nullable=False)
    client_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    client_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    counted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    warning_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)


class AttemptReview(Base):
    __tablename__ = "attempt_reviews"

    review_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("test_attempts.attempt_id"), nullable=False, unique=True)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    student_message: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    decided_by_staff_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    decided_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retake_attempt_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("test_attempts.attempt_id"), nullable=True)
