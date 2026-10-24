from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from institute_common.core.class_levels import CLASS_LEVELS, OTHER_MAX
from institute_common.core.taxonomy import sql_in
from institute_common.database import Base, UTCDateTime, utcnow


class Subject(Base):
    """Platform-wide subject catalogue (e.g. PHYSICS). Providers opt in via provider_subjects."""

    __tablename__ = "subjects"
    __table_args__ = (CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="subjects_status_check"),)

    subject_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class ProviderSubject(Base):
    __tablename__ = "provider_subjects"
    __table_args__ = (UniqueConstraint("provider_id", "subject_id", name="uq_provider_subject"),)

    provider_subject_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class BatchSubject(Base):
    __tablename__ = "batch_subjects"
    __table_args__ = (UniqueConstraint("batch_id", "subject_id", name="uq_batch_subject"),)

    batch_subject_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("batches.batch_id"), nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


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
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class EnrollmentBatchSubject(Base):
    """A subject the student takes in one batch membership (enrollment subject 0003). A membership WITHOUT rows takes every subject
    its batch offers (the original behaviour); with rows, only those subjects count for exam eligibility and teacher visibility."""

    __tablename__ = "enrollment_batch_subjects"
    __table_args__ = (UniqueConstraint("enrollment_batch_id", "subject_id", name="uq_enrollment_batch_subject"),)

    enrollment_batch_subject_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enrollment_batch_id: Mapped[UUID] = mapped_column(ForeignKey("enrollment_batches.enrollment_batch_id", ondelete="CASCADE"),
                                                      nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


def _year_check(col: str) -> str:
    """'YYYY-YYYY' with consecutive years; portable (PostgreSQL and the SQLite test database)."""
    return (f"{col} IS NULL OR (length({col}) = 9 AND substr({col}, 5, 1) = '-' "
            f"AND CAST(substr({col}, 6, 4) AS INTEGER) = CAST(substr({col}, 1, 4) AS INTEGER) + 1)")


class EnrollmentAcademicProfile(Base):
    """Class and Academic Year of ONE provider enrollment (enrollment 0004). Never on the global student identity: the same student can
    have a different Class at another provider. Exactly one row per enrollment; see core/class_levels.py for the values and matching."""

    __tablename__ = "enrollment_academic_profiles"
    __table_args__ = (
        CheckConstraint(f"class_level IN ({sql_in(CLASS_LEVELS)})", name="eap_class_level_check"),
        CheckConstraint(_year_check("academic_year") + " AND academic_year IS NOT NULL", name="eap_academic_year_check"),
        CheckConstraint("(class_level = 'OTHER' AND other_class_description IS NOT NULL) OR (class_level <> 'OTHER' AND other_class_description IS NULL)",
                        name="eap_other_description_check"),
    )

    enrollment_id: Mapped[str] = mapped_column(ForeignKey("enrollments.enrollment_id", ondelete="CASCADE"), primary_key=True)
    class_level: Mapped[str] = mapped_column(String(12), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(9), nullable=False)
    other_class_description: Mapped[str | None] = mapped_column(String(OTHER_MAX))
    updated_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class BatchAcademicDefault(Base):
    """Optional Class / Academic Year a batch suggests when students are enrolled into it (academics 0002). Only prefills the enrollment
    form: changing it never rewrites an enrollment's confirmed profile. A row exists only while at least one default is set."""

    __tablename__ = "batch_academic_defaults"
    __table_args__ = (
        CheckConstraint(f"default_class_level IS NULL OR default_class_level IN ({sql_in(CLASS_LEVELS)})", name="bad_class_level_check"),
        CheckConstraint(_year_check("default_academic_year"), name="bad_academic_year_check"),
        CheckConstraint("(default_class_level = 'OTHER' AND default_other_description IS NOT NULL) "
                        "OR ((default_class_level IS NULL OR default_class_level <> 'OTHER') AND default_other_description IS NULL)",
                        name="bad_other_description_check"),
    )

    batch_id: Mapped[UUID] = mapped_column(ForeignKey("batches.batch_id", ondelete="CASCADE"), primary_key=True)
    default_class_level: Mapped[str | None] = mapped_column(String(12))
    default_academic_year: Mapped[str | None] = mapped_column(String(9))
    default_other_description: Mapped[str | None] = mapped_column(String(OTHER_MAX))
    updated_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    reason: Mapped[str | None] = mapped_column(String(1000))
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column()
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    new_enrollment_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
