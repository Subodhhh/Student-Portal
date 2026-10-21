"""Three test types: Global Test contributions and Weekly Tests (additive)

  * global_test_contributions: a provider's exact paper-version submission for a Global Test, reviewed by the platform
    (PENDING -> APPROVED | REJECTED | WITHDRAWN) and, once approved, copied into an independent GLOBAL paper. Partial unique indexes:
    one active contribution per test/provider/subject, and at most one APPROVED contribution per test/subject.
  * test_series: a Weekly Test - one month of recurring tests with the Daily Test configuration.
  * exam_plans.series_id (nullable): the plan of one occurrence of a series; unique per (series_id, start_at) so generating a series
    twice never duplicates an occurrence. Existing plans keep NULL.
Nothing is dropped or rewritten. A fresh database gets these from the baseline (current models); this revision creates only what is
missing.

Revision ID: 0006_global_test_contributions
Revises: 0005_exam_targeted_tests
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType
from institute_common.migrations import existing_columns, existing_indexes

revision = "0006_global_test_contributions"
down_revision = "0005_exam_targeted_tests"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "test_series" not in tables:
        op.create_table(
            "test_series",
            sa.Column("series_id", sa.Uuid(), primary_key=True),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
            sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("month", sa.String(7), nullable=False),
            sa.Column("weekdays", JSONType, nullable=False),
            sa.Column("start_time", sa.String(5), nullable=False),
            sa.Column("timezone", sa.String(64), nullable=False),
            sa.Column("window_minutes", sa.Integer(), nullable=False),
            sa.Column("duration_minutes", sa.Integer(), nullable=False),
            sa.Column("generate_lead_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("marking_mode", sa.String(12), nullable=False, server_default="UNIFORM"),
            sa.Column("marks_correct", sa.Integer()),
            sa.Column("marks_wrong", sa.Integer()),
            sa.Column("exam_type", sa.String(80)),
            sa.Column("target_class", sa.String(80)),
            sa.Column("audience", JSONType, nullable=False),
            sa.Column("subjects", JSONType, nullable=False),
            sa.Column("client_request_id", sa.String(100), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("last_generated_at", sa.DateTime(timezone=True)),
            sa.Column("last_generation_error", sa.String(1000)),
            sa.Column("generation_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cancelled_at", sa.DateTime(timezone=True)),
            sa.Column("cancel_reason", sa.String(1000)),
            sa.UniqueConstraint("client_request_id", name="test_series_client_request_id_key"),
            sa.CheckConstraint("status IN ('ACTIVE','CANCELLED')", name="test_series_status_check"),
            sa.CheckConstraint("duration_minutes > 0 AND duration_minutes <= window_minutes AND window_minutes <= 1440",
                               name="test_series_window_check"),
            sa.CheckConstraint("generate_lead_minutes >= 0 AND generate_lead_minutes <= 10080", name="test_series_lead_check"),
            sa.CheckConstraint("marking_mode IN ('PER_QUESTION','UNIFORM')", name="test_series_marking_check"),
            sa.CheckConstraint("marking_mode = 'PER_QUESTION' OR (marks_correct > 0 AND marks_wrong >= 0)",
                               name="test_series_uniform_marks_check"),
        )
        op.create_index("ix_test_series_provider_id", "test_series", ["provider_id"])

    if "series_id" not in existing_columns(bind, "exam_plans"):
        with op.batch_alter_table("exam_plans") as b:
            b.add_column(sa.Column("series_id", sa.Uuid()))
            b.create_foreign_key("exam_plans_series_id_fkey", "test_series", ["series_id"], ["series_id"])
            b.create_unique_constraint("uq_exam_plan_series_start", ["series_id", "start_at"])
    if "ix_exam_plans_series_id" not in existing_indexes(bind, "exam_plans"):
        op.create_index("ix_exam_plans_series_id", "exam_plans", ["series_id"])

    if "global_test_contributions" not in tables:
        op.create_table(
            "global_test_contributions",
            sa.Column("contribution_id", sa.Uuid(), primary_key=True),
            sa.Column("test_id", sa.Uuid(), sa.ForeignKey("global_tests.test_id"), nullable=False),
            sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
            sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
            sa.Column("paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id"), nullable=False),
            sa.Column("note", sa.String(1000)),
            sa.Column("submitted_by", sa.Uuid(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("reviewed_by", sa.Uuid()),
            sa.Column("review_note", sa.String(1000)),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("copy_status", sa.String(20)),
            sa.Column("copy_error", sa.String(500)),
            sa.Column("global_paper_id", sa.Uuid(), sa.ForeignKey("papers.paper_id")),
            sa.Column("global_paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id")),
            sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','WITHDRAWN')", name="global_test_contributions_status_check"),
            sa.CheckConstraint("copy_status IS NULL OR copy_status IN ('PENDING','COPIED','FAILED')",
                               name="global_test_contributions_copy_check"),
        )
        for col in ("test_id", "provider_id", "subject_id"):
            op.create_index(f"ix_global_test_contributions_{col}", "global_test_contributions", [col])
        op.create_index("uq_global_test_contribution_active", "global_test_contributions", ["test_id", "provider_id", "subject_id"],
                        unique=True, postgresql_where=sa.text("status IN ('PENDING','APPROVED')"),
                        sqlite_where=sa.text("status IN ('PENDING','APPROVED')"))
        op.create_index("uq_global_test_contribution_approved", "global_test_contributions", ["test_id", "subject_id"],
                        unique=True, postgresql_where=sa.text("status = 'APPROVED'"), sqlite_where=sa.text("status = 'APPROVED'"))


def downgrade() -> None:
    # Forward-only by policy: contribution reviews and generated plans are history.
    pass
