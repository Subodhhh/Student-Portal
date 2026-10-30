"""Global Test paper choice (see the attempt_paper_selections migration). The eligible papers are the test service's
(student_paper_eligibility, immutable); this service keeps the student's choice and, at Start, the attempt's frozen snapshot."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now():
    return datetime.now(timezone.utc)


class PaperSelection(Base):
    """The student's current choice for a SELECTABLE subject; changeable until an attempt exists."""
    __tablename__ = "paper_selections"

    test_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    subject: Mapped[str] = mapped_column(String(100), primary_key=True)
    paper_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class AttemptSelectedPaper(Base):
    """The attempt's paper per subject, written with the attempt and never changed (a retake copies its original's rows)."""
    __tablename__ = "attempt_selected_papers"

    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("test_attempts.attempt_id"), primary_key=True)
    subject: Mapped[str] = mapped_column(String(100), primary_key=True)
    paper_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
