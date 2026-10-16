from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JSONType, UTCDateTime, utcnow


class ExamPlan(Base):
    __tablename__ = "exam_plans"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="exam_plans_status_check"),
        CheckConstraint("duration_minutes > 0", name="exam_plans_duration_check"),
    )

    plan_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    generate_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    auto_publish: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class ExamPlanBatch(Base):
    __tablename__ = "exam_plan_batches"
    __table_args__ = (UniqueConstraint("plan_id", "batch_id", name="uq_plan_batch"),)

    plan_batch_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("exam_plans.plan_id"), nullable=False, index=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("batches.batch_id"), nullable=False)


class ExamPlanSubject(Base):
    """One subject slot of a plan. Exactly one paper rule applies:
      paper_version_id -> that exact version, always;
      paper_id         -> that paper's latest PUBLISHED version at generation time.
    A slot may point at another provider's paper only if it was shared with this provider."""

    __tablename__ = "exam_plan_subjects"
    __table_args__ = (
        UniqueConstraint("plan_id", "position", name="uq_plan_position"),
        CheckConstraint("(paper_id IS NOT NULL) <> (paper_version_id IS NOT NULL)", name="exam_plan_subjects_rule_check"),
    )

    slot_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("exam_plans.plan_id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    paper_id: Mapped[UUID | None] = mapped_column(ForeignKey("papers.paper_id"))
    paper_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("paper_versions.paper_version_id"))


class ExamGenerationJob(Base):
    """Idempotency record: one job per (plan, run_key). Retrying never duplicates exams."""

    __tablename__ = "exam_generation_jobs"
    __table_args__ = (
        UniqueConstraint("plan_id", "run_key", name="uq_generation_run"),
        CheckConstraint("status IN ('RUNNING','SUCCEEDED','PARTIAL','FAILED')", name="exam_generation_jobs_status_check"),
    )

    job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("exam_plans.plan_id"), nullable=False, index=True)
    run_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    exam_id: Mapped[UUID | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(String(1000))
    requested_by: Mapped[UUID | None] = mapped_column()
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ExamGenerationEvent(Base):
    __tablename__ = "exam_generation_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONType)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = (
        CheckConstraint("status IN ('SCHEDULED','PUBLISHED','CANCELLED')", name="exams_status_check"),
        UniqueConstraint("plan_id", "run_key", name="uq_exam_run"),
    )

    exam_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("exam_plans.plan_id"), nullable=False, index=True)
    run_key: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SCHEDULED")
    cancelled_reason: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ExamVersion(Base):
    """Immutable manifest of exactly which paper versions make up the exam."""

    __tablename__ = "exam_versions"
    __table_args__ = (UniqueConstraint("exam_id", "version_no", name="uq_exam_version"),)

    exam_version_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    exam_id: Mapped[UUID] = mapped_column(ForeignKey("exams.exam_id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    total_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    missing_subjects: Mapped[list | None] = mapped_column(JSONType)
    generation_job_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ExamVersionPaper(Base):
    __tablename__ = "exam_version_papers"
    __table_args__ = (UniqueConstraint("exam_version_id", "position", name="uq_evp_position"),)

    evp_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    exam_version_id: Mapped[UUID] = mapped_column(ForeignKey("exam_versions.exam_version_id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(ForeignKey("paper_versions.paper_version_id"), nullable=False)
    source_provider_id: Mapped[UUID] = mapped_column(nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)


class StudentExamAssignment(Base):
    __tablename__ = "student_exam_assignments"
    __table_args__ = (
        UniqueConstraint("exam_version_id", "student_id", name="uq_assignment_student"),
        CheckConstraint("status IN ('ASSIGNED','MISSED','STARTED','COMPLETED')", name="student_exam_assignments_status_check"),
    )

    assignment_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    exam_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    exam_version_id: Mapped[UUID] = mapped_column(ForeignKey("exam_versions.exam_version_id"), nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    enrollment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ASSIGNED")
    status_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)   # last sync from the student attempt data
    result_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # student told their result is available
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ExamOutbox(Base):
    """Transactional outbox: publication to the Student test service is retried until
    confirmed; nothing reports success before the receiver acknowledges."""

    __tablename__ = "exam_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','SENT','FAILED')", name="exam_outbox_status_check"),
        CheckConstraint("operation IN ('PUBLISH','CANCEL')", name="exam_outbox_operation_check"),
        UniqueConstraint("exam_version_id", "operation", name="uq_outbox_exam_op"),
        Index("ix_exam_outbox_due", "status", "next_attempt_at"),
    )

    outbox_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    exam_version_id: Mapped[UUID] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False, default="PUBLISH")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Notification(Base):
    """Institute-side staff notifications (paper processing, missing papers, failures)."""

    __tablename__ = "institute_notifications"

    notification_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recipient_staff_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE")  # NONE|SENT|FAILED
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class StudentNotificationOutbox(Base):
    """Student inbox notifications waiting to be delivered to the Student notification service
    (durable, idempotent by dedupe_key, retried with backoff)."""

    __tablename__ = "student_notification_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','SENT','FAILED')", name="student_notification_outbox_status_check"),
        Index("ix_student_notification_outbox_due", "status", "next_attempt_at"),
    )

    outbox_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONType)
    student_ids: Mapped[list] = mapped_column(JSONType, nullable=False)
    provider_id: Mapped[UUID | None] = mapped_column()
    branch_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ResultCorrection(Base):
    """A Super Admin's correction of results (one student's marks, or the answer key followed by re-evaluation).
    The request, reason and actor are immutable (database trigger on PostgreSQL); only the execution state
    (status, outcome, error, applied_at) changes while the Student services are being called."""

    __tablename__ = "result_corrections"
    __table_args__ = (
        CheckConstraint("kind IN ('MARKS_ADJUSTMENT','ANSWER_KEY')", name="result_corrections_kind_check"),
        CheckConstraint("status IN ('PENDING','APPLIED','FAILED')", name="result_corrections_status_check"),
        Index("ix_result_corrections_exam", "exam_id", "created_at"),
    )

    correction_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    exam_id: Mapped[UUID] = mapped_column(ForeignKey("exams.exam_id"), nullable=False)
    exam_version_id: Mapped[UUID] = mapped_column(ForeignKey("exam_versions.exam_version_id"), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(nullable=False)
    branch_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    student_id: Mapped[str | None] = mapped_column(String(20))
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    request: Mapped[dict] = mapped_column(JSONType, nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor_staff_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    outcome: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(String(500))
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
