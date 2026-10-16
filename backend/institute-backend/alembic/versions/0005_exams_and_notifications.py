"""exam plans, generation jobs, exams/versions/manifest, assignments, outbox, notifications (additive)

Revision ID: 0005_exams
Revises: 0004_papers
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_exams"
down_revision = "0004_papers"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")
JSONT = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "exam_plans",
        sa.Column("plan_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("start_at", TS, nullable=False),
        sa.Column("end_at", TS, nullable=False),
        sa.Column("generate_at", TS),
        sa.Column("auto_publish", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="exam_plans_status_check"),
        sa.CheckConstraint("duration_minutes > 0", name="exam_plans_duration_check"),
    )
    op.create_index("ix_exam_plans_provider_id", "exam_plans", ["provider_id"])
    op.create_table(
        "exam_plan_batches",
        sa.Column("plan_batch_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("exam_plans.plan_id"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("batches.batch_id"), nullable=False),
        sa.UniqueConstraint("plan_id", "batch_id", name="uq_plan_batch"),
    )
    op.create_index("ix_exam_plan_batches_plan_id", "exam_plan_batches", ["plan_id"])
    op.create_table(
        "exam_plan_subjects",
        sa.Column("slot_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("exam_plans.plan_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("paper_id", sa.Uuid(), sa.ForeignKey("papers.paper_id")),
        sa.Column("paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id")),
        sa.UniqueConstraint("plan_id", "position", name="uq_plan_position"),
        sa.CheckConstraint("(paper_id IS NOT NULL) <> (paper_version_id IS NOT NULL)", name="exam_plan_subjects_rule_check"),
    )
    op.create_index("ix_exam_plan_subjects_plan_id", "exam_plan_subjects", ["plan_id"])
    op.create_table(
        "exam_generation_jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("exam_plans.plan_id"), nullable=False),
        sa.Column("run_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="RUNNING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("exam_id", sa.Uuid()),
        sa.Column("error", sa.String(1000)),
        sa.Column("requested_by", sa.Uuid()),
        sa.Column("started_at", TS, nullable=False, server_default=NOW),
        sa.Column("finished_at", TS),
        sa.UniqueConstraint("plan_id", "run_key", name="uq_generation_run"),
        sa.CheckConstraint("status IN ('RUNNING','SUCCEEDED','PARTIAL','FAILED')", name="exam_generation_jobs_status_check"),
    )
    op.create_index("ix_exam_generation_jobs_plan_id", "exam_generation_jobs", ["plan_id"])
    op.create_table(
        "exam_generation_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("details", JSONT),
        sa.Column("occurred_at", TS, nullable=False, server_default=NOW),
    )
    op.create_index("ix_exam_generation_events_job_id", "exam_generation_events", ["job_id"])
    op.create_table(
        "exams",
        sa.Column("exam_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("exam_plans.plan_id"), nullable=False),
        sa.Column("run_key", sa.String(120), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="SCHEDULED"),
        sa.Column("cancelled_reason", sa.String(1000)),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("plan_id", "run_key", name="uq_exam_run"),
        sa.CheckConstraint("status IN ('SCHEDULED','PUBLISHED','CANCELLED')", name="exams_status_check"),
    )
    op.create_index("ix_exams_plan_id", "exams", ["plan_id"])
    op.create_index("ix_exams_provider_id", "exams", ["provider_id"])
    op.create_table(
        "exam_versions",
        sa.Column("exam_version_id", sa.Uuid(), primary_key=True),
        sa.Column("exam_id", sa.Uuid(), sa.ForeignKey("exams.exam_id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("start_at", TS, nullable=False),
        sa.Column("end_at", TS, nullable=False),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("total_marks", sa.Numeric(8, 2), nullable=False),
        sa.Column("missing_subjects", JSONT),
        sa.Column("generation_job_id", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("published_at", TS),
        sa.UniqueConstraint("exam_id", "version_no", name="uq_exam_version"),
    )
    op.create_index("ix_exam_versions_exam_id", "exam_versions", ["exam_id"])
    op.create_table(
        "exam_version_papers",
        sa.Column("evp_id", sa.Uuid(), primary_key=True),
        sa.Column("exam_version_id", sa.Uuid(), sa.ForeignKey("exam_versions.exam_version_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id"), nullable=False),
        sa.Column("source_provider_id", sa.Uuid(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("total_marks", sa.Numeric(8, 2), nullable=False),
        sa.UniqueConstraint("exam_version_id", "position", name="uq_evp_position"),
    )
    op.create_index("ix_exam_version_papers_exam_version_id", "exam_version_papers", ["exam_version_id"])
    op.create_table(
        "student_exam_assignments",
        sa.Column("assignment_id", sa.Uuid(), primary_key=True),
        sa.Column("exam_id", sa.Uuid(), nullable=False),
        sa.Column("exam_version_id", sa.Uuid(), sa.ForeignKey("exam_versions.exam_version_id"), nullable=False),
        sa.Column("student_id", sa.String(20), nullable=False),
        sa.Column("enrollment_id", sa.String(36), nullable=False),
        sa.Column("batch_id", sa.Uuid()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ASSIGNED"),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("exam_version_id", "student_id", name="uq_assignment_student"),
        sa.CheckConstraint("status IN ('ASSIGNED','MISSED','STARTED','COMPLETED')", name="student_exam_assignments_status_check"),
    )
    op.create_index("ix_student_exam_assignments_exam_id", "student_exam_assignments", ["exam_id"])
    op.create_index("ix_student_exam_assignments_student_id", "student_exam_assignments", ["student_id"])
    op.create_table(
        "exam_outbox",
        sa.Column("outbox_id", sa.Uuid(), primary_key=True),
        sa.Column("exam_version_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False, server_default="PUBLISH"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("sent_at", TS),
        sa.UniqueConstraint("exam_version_id", "operation", name="uq_outbox_exam_op"),
        sa.CheckConstraint("status IN ('PENDING','SENT','FAILED')", name="exam_outbox_status_check"),
        sa.CheckConstraint("operation IN ('PUBLISH','CANCEL')", name="exam_outbox_operation_check"),
    )
    op.create_index("ix_exam_outbox_due", "exam_outbox", ["status", "next_attempt_at"])
    op.create_table(
        "institute_notifications",
        sa.Column("notification_id", sa.Uuid(), primary_key=True),
        sa.Column("recipient_staff_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid()),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("email_status", sa.String(20), nullable=False, server_default="NONE"),
        sa.Column("read_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_institute_notifications_recipient_staff_id", "institute_notifications", ["recipient_staff_id"])
    if op.get_bind().dialect.name == "postgresql":
        # The manifest is immutable. exam_versions may still have published_at set when a SCHEDULED exam
        # is published, so its trigger only guards the manifest columns (and DELETE).
        op.execute("CREATE TRIGGER exam_versions_immutable BEFORE UPDATE OF exam_id, version_no, duration_minutes, "
                   "start_at, end_at, total_questions, total_marks, missing_subjects, generation_job_id OR DELETE "
                   "ON exam_versions FOR EACH ROW EXECUTE FUNCTION institute_history_immutable();")
        op.execute("CREATE TRIGGER exam_version_papers_immutable BEFORE UPDATE OR DELETE ON exam_version_papers "
                   "FOR EACH ROW EXECUTE FUNCTION institute_history_immutable();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in ("exam_versions", "exam_version_papers"):
            op.execute(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
    for t in ("institute_notifications", "exam_outbox", "student_exam_assignments", "exam_version_papers", "exam_versions",
              "exams", "exam_generation_events", "exam_generation_jobs", "exam_plan_subjects", "exam_plan_batches", "exam_plans"):
        op.drop_table(t)
