from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, utcnow


class Subject(Base):
    """Platform-wide subject catalogue (e.g. PHYSICS). Providers opt in via provider_subjects."""

    __tablename__ = "subjects"
    __table_args__ = (CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="subjects_status_check"),)

    subject_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class ProviderSubject(Base):
    __tablename__ = "provider_subjects"
    __table_args__ = (UniqueConstraint("provider_id", "subject_id", name="uq_provider_subject"),)

    provider_subject_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class BatchSubject(Base):
    __tablename__ = "batch_subjects"
    __table_args__ = (UniqueConstraint("batch_id", "subject_id", name="uq_batch_subject"),)

    batch_subject_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("batches.batch_id"), nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


_NO_BATCH = text("batch_id IS NULL")
_BATCH = text("batch_id IS NOT NULL")


class TeacherSubjectAssignment(Base):
    """Teaching assignment: the only way a TEACHER reaches branch/subject/batch data.
    batch_id NULL means every batch of that subject in the branch."""

    __tablename__ = "teacher_subject_assignments"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="tsa_status_check"),
        Index("uq_tsa_all_batches", "staff_id", "branch_id", "subject_id", unique=True,
              postgresql_where=_NO_BATCH, sqlite_where=_NO_BATCH),
        Index("uq_tsa_batch", "staff_id", "branch_id", "subject_id", "batch_id", unique=True,
              postgresql_where=_BATCH, sqlite_where=_BATCH),
    )

    teaching_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("staff.staff_id"), nullable=False, index=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column(ForeignKey("batches.batch_id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class EnrollmentEvent(Base):
    """Append-only history for enrollments (the shared enrollments tables are mutable)."""

    __tablename__ = "enrollment_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enrollment_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider_id: Mapped[UUID] = mapped_column(nullable=False)
    branch_id: Mapped[UUID] = mapped_column(nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column()
    actor_staff_id: Mapped[UUID | None] = mapped_column()
    reason: Mapped[str | None] = mapped_column(String(1000))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class EnrollmentTransfer(Base):
    """Cross-branch moves need Provider Admin approval."""

    __tablename__ = "enrollment_transfers"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="enrollment_transfers_status_check"),
    )

    transfer_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enrollment_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    from_branch_id: Mapped[UUID] = mapped_column(nullable=False)
    to_branch_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    reason: Mapped[str | None] = mapped_column(String(1000))
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column()
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    new_enrollment_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
