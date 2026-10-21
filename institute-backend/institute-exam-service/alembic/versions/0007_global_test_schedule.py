"""Global Tests: central schedule, provider paper submissions (additive)

  * global_tests: `kind` (SCHEDULE | SECTIONS; existing tests become SECTIONS and keep working), exam-level marking
    (marking_mode, marks_correct, marks_wrong) and `generate_lead_minutes` (papers are accepted until then).
  * global_test_subjects: the subjects of a SCHEDULE test (no platform paper needed).
  * global_test_occurrences: centrally scheduled sittings, DRAFT -> PUBLISHED (or CANCELLED), unique per (test, start).
  * global_paper_submissions: a provider's paper for one subject of one occurrence, for an explicit authorized audience.
  * exam_plans.global_occurrence_id (nullable): the plans generated for an occurrence (one per provider and paper set).
Nothing is dropped or rewritten. A fresh database gets these from the baseline (current models); this revision creates only what is
missing.

Revision ID: 0007_global_test_schedule
Revises: 0006_global_test_contributions
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType
from institute_common.migrations import existing_checks, existing_columns, existing_indexes

revision = "0007_global_test_schedule"
down_revision = "0006_global_test_contributions"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    cols = existing_columns(bind, "global_tests")
    with op.batch_alter_table("global_tests") as b:
        if "kind" not in cols:
            b.add_column(sa.Column("kind", sa.String(10), nullable=False, server_default="SECTIONS"))
        if "marking_mode" not in cols:
            b.add_column(sa.Column("marking_mode", sa.String(12), nullable=False, server_default="PER_QUESTION"))
        if "marks_correct" not in cols:
            b.add_column(sa.Column("marks_correct", sa.Integer()))
        if "marks_wrong" not in cols:
            b.add_column(sa.Column("marks_wrong", sa.Integer()))
        if "generate_lead_minutes" not in cols:
            b.add_column(sa.Column("generate_lead_minutes", sa.Integer(), nullable=False, server_default="60"))
    checks = existing_checks(bind, "global_tests")
    for name, rule in (("global_tests_kind_check", "kind IN ('SCHEDULE','SECTIONS')"),
                       ("global_tests_marking_check", "marking_mode IN ('PER_QUESTION','UNIFORM')"),
                       ("global_tests_uniform_marks_check", "marking_mode = 'PER_QUESTION' OR (marks_correct > 0 AND marks_wrong >= 0)"),
                       ("global_tests_lead_check", "generate_lead_minutes >= 0 AND generate_lead_minutes <= 10080")):
        if name not in checks:
            op.create_check_constraint(name, "global_tests", rule)

    if "global_test_subjects" not in tables:
        op.create_table(
            "global_test_subjects",
            sa.Column("gts_id", sa.Uuid(), primary_key=True),
            sa.Column("test_id", sa.Uuid(), sa.ForeignKey("global_tests.test_id"), nullable=False),
            sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.UniqueConstraint("test_id", "subject_id", name="uq_global_test_subject"),
            sa.UniqueConstraint("test_id", "position", name="uq_global_test_subject_position"),
        )
        op.create_index("ix_global_test_subjects_test_id", "global_test_subjects", ["test_id"])

    if "global_test_occurrences" not in tables:
        op.create_table(
            "global_test_occurrences",
            sa.Column("occurrence_id", sa.Uuid(), primary_key=True),
            sa.Column("test_id", sa.Uuid(), sa.ForeignKey("global_tests.test_id"), nullable=False),
            sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
            sa.Column("created_by", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("published_by", sa.Uuid()),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.Column("generated_at", sa.DateTime(timezone=True)),
            sa.Column("generation_error", sa.String(1000)),
            sa.Column("cancelled_at", sa.DateTime(timezone=True)),
            sa.Column("cancel_reason", sa.String(1000)),
            sa.UniqueConstraint("test_id", "start_at", name="uq_global_test_occurrence_start"),
            sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','CANCELLED')", name="global_test_occurrences_status_check"),
            sa.CheckConstraint("end_at > start_at", name="global_test_occurrences_window_check"),
        )
        op.create_index("ix_global_test_occurrences_test_id", "global_test_occurrences", ["test_id"])

    if "global_paper_submissions" not in tables:
        op.create_table(
            "global_paper_submissions",
            sa.Column("submission_id", sa.Uuid(), primary_key=True),
            sa.Column("occurrence_id", sa.Uuid(), sa.ForeignKey("global_test_occurrences.occurrence_id"), nullable=False),
            sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
            sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
            sa.Column("paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id"), nullable=False),
            sa.Column("audience", JSONType, nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("client_request_id", sa.String(100), nullable=False),
            sa.Column("submitted_by", sa.Uuid(), nullable=False),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("withdrawn_by", sa.Uuid()),
            sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
            sa.Column("withdraw_reason", sa.String(1000)),
            sa.UniqueConstraint("client_request_id", name="global_paper_submissions_client_request_id_key"),
            sa.CheckConstraint("status IN ('ACTIVE','WITHDRAWN')", name="global_paper_submissions_status_check"),
        )
        op.create_index("ix_global_paper_submissions_occurrence", "global_paper_submissions", ["occurrence_id", "provider_id", "subject_id"])
        op.create_index("ix_global_paper_submissions_provider_id", "global_paper_submissions", ["provider_id"])

    if "global_occurrence_id" not in existing_columns(bind, "exam_plans"):
        with op.batch_alter_table("exam_plans") as b:
            b.add_column(sa.Column("global_occurrence_id", sa.Uuid()))
            b.create_foreign_key("exam_plans_global_occurrence_id_fkey", "global_test_occurrences", ["global_occurrence_id"], ["occurrence_id"])
    if "ix_exam_plans_global_occurrence_id" not in existing_indexes(bind, "exam_plans"):
        op.create_index("ix_exam_plans_global_occurrence_id", "exam_plans", ["global_occurrence_id"])


def downgrade() -> None:
    # Forward-only by policy: schedules, submissions and the exams generated from them are history.
    pass
