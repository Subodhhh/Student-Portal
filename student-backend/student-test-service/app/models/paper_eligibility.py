from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# CONFLICT only for Global Test dates generated before students could choose (never produced any more)
ELIGIBILITY_STATUSES = ("PAPER", "SELECTABLE", "NOT_ENROLLED", "NO_SUBMISSION", "NOT_APPROVED", "NOT_COVERED", "CONFLICT")


class StudentPaperEligibility(Base):
    """A paper-choice Global Test student's eligibility, frozen by the Institute at generation and written once on delivery (never
    changed afterwards): one row per eligible paper of a subject (PAPER: exactly one, SELECTABLE: two or more), or one row without a
    paper giving why the subject is unavailable. The student's choice is kept by the attempt service, not here."""

    __tablename__ = "student_paper_eligibility"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PAPER','SELECTABLE','NOT_ENROLLED','NO_SUBMISSION','NOT_APPROVED','NOT_COVERED','CONFLICT')",
            name="ck_student_paper_eligibility_status",
        ),
        CheckConstraint(
            "(status IN ('PAPER','SELECTABLE')) = (paper_id IS NOT NULL)",
            name="ck_student_paper_eligibility_paper",
        ),
        UniqueConstraint("test_id", "student_id", "subject", "paper_id", name="uq_student_paper_eligibility_paper"),
        # at most one "unavailable" row per subject (NULL paper_ids are never equal in the constraint above)
        Index("uq_student_paper_eligibility_unavailable", "test_id", "student_id", "subject", unique=True,
              postgresql_where=text("paper_id IS NULL"), sqlite_where=text("paper_id IS NULL")),
    )

    eligibility_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    test_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tests.test_id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
