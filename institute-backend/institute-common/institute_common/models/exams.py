from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from institute_common.core.taxonomy import sql_in
from institute_common.database import Base, JSONType, UTCDateTime, utcnow

AUDIENCE_TYPES = ("BATCHES", "BRANCH", "INSTITUTE", "STUDENTS", "TARGETED")


class ExamPlan(Base):
    __tablename__ = "exam_plans"
    __table_args__ = (
        # DRAFT plans are saved from the test-creation wizard and never generate until activated.
        CheckConstraint("status IN ('DRAFT','ACTIVE','INACTIVE')", name="exam_plans_status_check"),
        CheckConstraint("duration_minutes > 0", name="exam_plans_duration_check"),
        CheckConstraint(f"audience_type IN ({sql_in(AUDIENCE_TYPES)})", name="exam_plans_audience_check"),
        CheckConstraint("marking_mode IN ('PER_QUESTION','UNIFORM')", name="exam_plans_marking_check"),
        CheckConstraint("marking_mode = 'PER_QUESTION' OR (marks_correct > 0 AND marks_wrong >= 0)", name="exam_plans_uniform_marks_check"),
        UniqueConstraint("series_id", "start_at", name="uq_exam_plan_series_start"),
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
    auto_publish: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    # Who sits the exam (resolved at every generation from current enrollments; see exam_generation.eligible_students):
    #   BATCHES   the plan's batches (exam_plan_batches) in its branch - the original behaviour and the default
    #   BRANCH    every active batch of the plan's branch
    #   INSTITUTE every active batch of every branch of the provider (provider-level permission required)
    #   STUDENTS  the listed students (exam_plan_students) with an active enrollment at the provider
    #   TARGETED  Test -> Create Test: the UNION of whole branches (exam_plan_branches), batches of any of the provider's branches
    #             (exam_plan_batches) and listed students (exam_plan_students); one assignment per student
    audience_type: Mapped[str] = mapped_column(String(12), nullable=False, default="BATCHES", server_default="BATCHES")
    # PER_QUESTION: each question's own marks (default). UNIFORM: +marks_correct / -marks_wrong for every question of the exam.
    marking_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="PER_QUESTION", server_default="PER_QUESTION")
    marks_correct: Mapped[int | None] = mapped_column(Integer)
    marks_wrong: Mapped[int | None] = mapped_column(Integer)
    exam_type: Mapped[str | None] = mapped_column(String(80))
    target_class: Mapped[str | None] = mapped_column(String(80))
    # Set when the plan schedules a published Global Test: its papers, duration and marking are locked to the test's.
    global_test_id: Mapped[UUID | None] = mapped_column(ForeignKey("global_tests.test_id"), index=True)
    # Set on the plans generated for one occurrence of a SCHEDULE Global Test (one plan per provider and set of submitted papers).
    global_occurrence_id: Mapped[UUID | None] = mapped_column(ForeignKey("global_test_occurrences.occurrence_id"), index=True)
    # Idempotency key of the Create Test request that made this plan: a retried publication finds it instead of duplicating it.
    client_request_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    # Set on the plans a Weekly Test (test_series) generated: one plan per occurrence, unique per start time.
    series_id: Mapped[UUID | None] = mapped_column(ForeignKey("test_series.series_id"), index=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class ExamPlanStudent(Base):
    """Explicitly selected students of a STUDENTS-audience plan (global Student IDs; never a copy of the identity)."""

    __tablename__ = "exam_plan_students"
    __table_args__ = (UniqueConstraint("plan_id", "student_id", name="uq_exam_plan_student"),)

    plan_student_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("exam_plans.plan_id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)


class ExamPlanBranch(Base):
    """Whole branches targeted by a TARGETED-audience plan (every active batch member of the branch)."""

    __tablename__ = "exam_plan_branches"
    __table_args__ = (UniqueConstraint("plan_id", "branch_id", name="uq_exam_plan_branch"),)

    plan_branch_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("exam_plans.plan_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)


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
    A slot may point at another provider's paper only if it was shared with this provider, or (unified Global Test plans) if it is
    the paper of `source_submission_id`, an ACTIVE submission of the plan's occurrence."""

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
    source_submission_id: Mapped[UUID | None] = mapped_column(ForeignKey("global_paper_submissions.submission_id"))


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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING", server_default="RUNNING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    exam_id: Mapped[UUID | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(String(1000))
    requested_by: Mapped[UUID | None] = mapped_column()
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ExamGenerationEvent(Base):
    __tablename__ = "exam_generation_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONType)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SCHEDULED", server_default="SCHEDULED")
    cancelled_reason: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


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
    # Frozen configuration used for this exam (marking, policies, audience, source Global Test); immutable like the manifest.
    settings: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ExamVersionPaper(Base):
    __tablename__ = "exam_version_papers"
    __table_args__ = (UniqueConstraint("exam_version_id", "position", name="uq_evp_position"),)

    evp_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    exam_version_id: Mapped[UUID] = mapped_column(ForeignKey("exam_versions.exam_version_id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(ForeignKey("paper_versions.paper_version_id"), nullable=False)
    source_provider_id: Mapped[UUID | None] = mapped_column()   # NULL: a GLOBAL (platform) paper
    source_submission_id: Mapped[UUID | None] = mapped_column()  # set only in unified Global Test exams (provider, branch, audience)
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ASSIGNED", server_default="ASSIGNED")
    status_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)   # last sync from the student attempt data
    result_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # student told their result is available
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


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
    operation: Mapped[str] = mapped_column(String(10), nullable=False, default="PUBLISH", server_default="PUBLISH")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
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
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE", server_default="NONE")  # NONE|SENT|FAILED
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    outcome: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(String(500))
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ExamNotificationOutbox(Base):
    """Staff notifications raised by the exam service, delivered to the operations service (owner of the inbox) with retries."""

    __tablename__ = "exam_notification_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','SENT','FAILED')", name="exam_notification_outbox_status_check"),
        Index("ix_exam_notification_outbox_due", "status", "next_attempt_at"),
    )

    outbox_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class CompositeRequest(Base):
    """A composite test being assembled across providers: the initiating provider names the subjects, each invited provider contributes
    one of its published papers (which also shares that paper with the initiator)."""

    __tablename__ = "composite_requests"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','COMPLETE','CANCELLED','EXPIRED')", name="composite_requests_status_check"),
        UniqueConstraint("created_by", "idempotency_key", name="uq_composite_idempotency"),
        Index("uq_composite_open_name", "initiator_branch_id", "name", unique=True,
              postgresql_where=text("status = 'OPEN'"), sqlite_where=text("status = 'OPEN'")),
        Index("ix_composite_requests_open_deadline", "respond_by", postgresql_where=text("status = 'OPEN' AND respond_by IS NOT NULL")),
    )

    request_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
    initiator_provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    initiator_branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN", server_default="OPEN")
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(1000))
    # Optional response deadline: pending invitations expire when it passes (worker), late contributions are refused.
    respond_by: Mapped[datetime | None] = mapped_column(UTCDateTime)


class CompositeRequestSlot(Base):
    __tablename__ = "composite_request_slots"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','CONTRIBUTED','DECLINED','EXPIRED')", name="composite_request_slots_status_check"),
        UniqueConstraint("request_id", "subject_id", name="uq_composite_slot_subject"),
    )

    slot_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("composite_requests.request_id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")   # order in which the initiator listed the subjects
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    invited_provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    paper_id: Mapped[UUID | None] = mapped_column()
    paper_version_id: Mapped[UUID | None] = mapped_column()
    responded_by: Mapped[UUID | None] = mapped_column()
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decline_reason: Mapped[str | None] = mapped_column(String(1000))


class GlobalTest(Base):
    """A platform-published test (Global Test) that providers schedule for their own students.

    DRAFT -> PUBLISHED -> WITHDRAWN. Once PUBLISHED its sections (exact GLOBAL paper versions), duration and marking never change:
    providers schedule it through an exam plan locked to those versions, and a different test is always a new record
    (optionally `derived_from_test_id`). Withdrawing stops new schedules only; exams already generated keep their manifest."""

    __tablename__ = "global_tests"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','PUBLISHED','WITHDRAWN')", name="global_tests_status_check"),
        CheckConstraint("duration_minutes > 0 AND duration_minutes <= 720", name="global_tests_duration_check"),
        CheckConstraint("kind IN ('SCHEDULE','SECTIONS')", name="global_tests_kind_check"),
        CheckConstraint("marking_mode IN ('PER_QUESTION','UNIFORM')", name="global_tests_marking_check"),
        CheckConstraint("marking_mode = 'PER_QUESTION' OR (marks_correct > 0 AND marks_wrong >= 0)", name="global_tests_uniform_marks_check"),
        CheckConstraint("generate_lead_minutes >= 0 AND generate_lead_minutes <= 10080", name="global_tests_lead_check"),
    )

    test_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    exam_type: Mapped[str | None] = mapped_column(String(80))
    target_class: Mapped[str | None] = mapped_column(String(80))
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT", server_default="DRAFT")
    derived_from_test_id: Mapped[UUID | None] = mapped_column(ForeignKey("global_tests.test_id"))
    # SCHEDULE: the platform defines subjects, marking and a central schedule (global_test_occurrences); every provider submits its own
    # papers per occurrence and audience (global_paper_submissions). SECTIONS: the earlier model (one set of platform papers, providers
    # schedule it themselves), kept for existing tests.
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="SCHEDULE", server_default="SECTIONS")
    marking_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="PER_QUESTION", server_default="PER_QUESTION")
    marks_correct: Mapped[int | None] = mapped_column(Integer)
    marks_wrong: Mapped[int | None] = mapped_column(Integer)
    # Papers are accepted until `generate_lead_minutes` before an occurrence opens; then its exams are generated and never change.
    generate_lead_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    published_by: Mapped[UUID | None] = mapped_column()
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class GlobalTestSection(Base):
    """One subject section of a global test: an exact, approved GLOBAL paper version."""

    __tablename__ = "global_test_sections"
    __table_args__ = (
        UniqueConstraint("test_id", "position", name="uq_global_test_section_position"),
        UniqueConstraint("test_id", "subject_id", name="uq_global_test_section_subject"),
    )

    section_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    test_id: Mapped[UUID] = mapped_column(ForeignKey("global_tests.test_id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(ForeignKey("paper_versions.paper_version_id"), nullable=False)

class GlobalTestContribution(Base):
    """A provider's exact paper-version submission for a Global Test.

    PENDING -> APPROVED | REJECTED | WITHDRAWN (withdrawn by the provider while still pending). Recording or approving a contribution
    never mutates a GlobalTest or its sections: an approved contribution is copied into an independent GLOBAL paper (paper service),
    which goes through the normal paper approval, and only a platform administrator places it in a DRAFT test.

    Database rules (partial unique indexes, enforced under concurrency):
      * one active (PENDING/APPROVED) contribution per test, provider and subject - a retry finds it instead of duplicating it;
      * at most one APPROVED contribution per test and subject - competing providers are a conflict the platform resolves explicitly."""

    __tablename__ = "global_test_contributions"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','WITHDRAWN')", name="global_test_contributions_status_check"),
        CheckConstraint("copy_status IS NULL OR copy_status IN ('PENDING','COPIED','FAILED')", name="global_test_contributions_copy_check"),
        Index("uq_global_test_contribution_active", "test_id", "provider_id", "subject_id", unique=True,
              postgresql_where=text("status IN ('PENDING','APPROVED')"), sqlite_where=text("status IN ('PENDING','APPROVED')")),
        Index("uq_global_test_contribution_approved", "test_id", "subject_id", unique=True,
              postgresql_where=text("status = 'APPROVED'"), sqlite_where=text("status = 'APPROVED'")),
    )

    contribution_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    test_id: Mapped[UUID] = mapped_column(ForeignKey("global_tests.test_id"), nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False, index=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    paper_version_id: Mapped[UUID] = mapped_column(ForeignKey("paper_versions.paper_version_id"), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
    submitted_by: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    reviewed_by: Mapped[UUID | None] = mapped_column()
    review_note: Mapped[str | None] = mapped_column(String(1000))
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The independent GLOBAL copy made after approval (NULL until the paper service confirmed it).
    copy_status: Mapped[str | None] = mapped_column(String(20))
    copy_error: Mapped[str | None] = mapped_column(String(500))
    global_paper_id: Mapped[UUID | None] = mapped_column(ForeignKey("papers.paper_id"))
    global_paper_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("paper_versions.paper_version_id"))


class TestSeries(Base):
    """A Weekly Test: one month of recurring tests planned at once.

    It holds the same configuration as a Daily Test (audience, subjects with their papers, duration, marking, window) plus the
    recurrence (weekdays of one month at a local time in an IANA time zone). Generating the series creates one ordinary exam plan per
    occurrence (`exam_plans.series_id`); the unique (series_id, start_at) constraint makes generation safe to retry. Each plan then
    generates its exam at `generate_at` through the normal generator, so missing papers, notifications and results behave exactly as
    for every other plan. Plans already created are never changed by a later edit or a new generation."""

    __tablename__ = "test_series"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','CANCELLED')", name="test_series_status_check"),
        CheckConstraint("duration_minutes > 0 AND duration_minutes <= window_minutes AND window_minutes <= 1440", name="test_series_window_check"),
        CheckConstraint("generate_lead_minutes >= 0 AND generate_lead_minutes <= 10080", name="test_series_lead_check"),
        CheckConstraint("marking_mode IN ('PER_QUESTION','UNIFORM')", name="test_series_marking_check"),
        CheckConstraint("marking_mode = 'PER_QUESTION' OR (marks_correct > 0 AND marks_wrong >= 0)", name="test_series_uniform_marks_check"),
    )

    series_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    month: Mapped[str] = mapped_column(String(7), nullable=False)            # YYYY-MM
    weekdays: Mapped[list] = mapped_column(JSONType, nullable=False)          # 0 = Monday ... 6 = Sunday
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)       # HH:MM local time
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    generate_lead_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    marking_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="UNIFORM", server_default="UNIFORM")
    marks_correct: Mapped[int | None] = mapped_column(Integer)
    marks_wrong: Mapped[int | None] = mapped_column(Integer)
    exam_type: Mapped[str | None] = mapped_column(String(80))
    target_class: Mapped[str | None] = mapped_column(String(80))
    audience: Mapped[dict] = mapped_column(JSONType, nullable=False)          # TestAudienceIn as validated at creation
    subjects: Mapped[list] = mapped_column(JSONType, nullable=False)          # [{subject_id, paper_id}] in order
    client_request_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    last_generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_generation_error: Mapped[str | None] = mapped_column(String(1000))
    generation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(1000))


class GlobalTestSubject(Base):
    """A subject of a SCHEDULE Global Test. Each provider supplies its own paper for it, per occurrence and audience."""

    __tablename__ = "global_test_subjects"
    __table_args__ = (
        UniqueConstraint("test_id", "subject_id", name="uq_global_test_subject"),
        UniqueConstraint("test_id", "position", name="uq_global_test_subject_position"),
    )

    gts_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    test_id: Mapped[UUID] = mapped_column(ForeignKey("global_tests.test_id"), nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class GlobalTestOccurrence(Base):
    """One centrally scheduled sitting of a Global Test (date, window). DRAFT occurrences may be edited or deleted; publishing makes
    them visible to providers, who then submit papers until the submission deadline (start - generate_lead_minutes). At the deadline
    the exams are generated (once: generated_at) and never change; a published occurrence that was not generated may be cancelled."""

    __tablename__ = "global_test_occurrences"
    __table_args__ = (
        UniqueConstraint("test_id", "start_at", name="uq_global_test_occurrence_start"),
        CheckConstraint("status IN ('DRAFT','PUBLISHED','CANCELLED')", name="global_test_occurrences_status_check"),
        CheckConstraint("end_at > start_at", name="global_test_occurrences_window_check"),
    )

    occurrence_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    test_id: Mapped[UUID] = mapped_column(ForeignKey("global_tests.test_id"), nullable=False, index=True)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT", server_default="DRAFT")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    published_by: Mapped[UUID | None] = mapped_column()
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    generation_error: Mapped[str | None] = mapped_column(String(1000))
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(1000))


class GlobalPaperSubmission(Base):
    """A provider's paper for one subject of one occurrence, for an explicit audience it is authorized for (the Create Test audience:
    whole branches, batches, individual students). Each student receives the paper of the submission that covers them; two ACTIVE
    submissions of one provider for the same occurrence and subject may never cover the same student (checked at submission and again
    at generation, where an ambiguous student gets neither paper and is reported)."""

    __tablename__ = "global_paper_submissions"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','WITHDRAWN')", name="global_paper_submissions_status_check"),
        Index("ix_global_paper_submissions_occurrence", "occurrence_id", "provider_id", "subject_id"),
    )

    submission_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    occurrence_id: Mapped[UUID] = mapped_column(ForeignKey("global_test_occurrences.occurrence_id"), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)      # home branch of the audience
    paper_version_id: Mapped[UUID] = mapped_column(ForeignKey("paper_versions.paper_version_id"), nullable=False)
    audience: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    client_request_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    submitted_by: Mapped[UUID] = mapped_column(nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    withdrawn_by: Mapped[UUID | None] = mapped_column()
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    withdraw_reason: Mapped[str | None] = mapped_column(String(1000))
