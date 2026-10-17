"""Global Daily Tests, plan audiences, exam-level marking and frozen exam settings (additive)

  * global_tests / global_test_sections: platform-published tests built from exact GLOBAL paper versions (new tables).
  * exam_plans: DRAFT status, audience_type (BATCHES default = existing behaviour, BRANCH, INSTITUTE, STUDENTS), marking_mode
    (PER_QUESTION default = existing behaviour, UNIFORM with marks_correct / marks_wrong), exam type / class, global_test_id.
  * exam_plan_students: the students of a STUDENTS-audience plan (new table).
  * exam_versions.settings: the configuration an exam was generated with; added to the append-only trigger like the manifest.
  * exam_version_papers.source_provider_id: nullable (a GLOBAL paper has no provider).
  * PostgreSQL guards: a global test and its sections change only as a DRAFT; publishing is allowed, then the only change is
    PUBLISHED -> WITHDRAWN (global_tests_guard, global_test_sections_guard; see institute_common.migrations).
Existing plans keep working unchanged: every new column is nullable or defaults to the previous behaviour.

Revision ID: 0004_exam_tests_audience
Revises: 0003_exam_composite_expiry
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType
from institute_common.migrations import ensure_triggers, existing_checks, existing_columns, nullable

revision = "0004_exam_tests_audience"
down_revision = "0003_exam_composite_expiry"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())   # a fresh database already has the new tables from the baseline
    if "global_tests" not in tables:
        _create_global_tests()
    _alter_plans(bind)
    if "exam_plan_students" not in tables:
        op.create_table(
            "exam_plan_students",
            sa.Column("plan_student_id", sa.Uuid(), primary_key=True),
            sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("exam_plans.plan_id"), nullable=False),
            sa.Column("student_id", sa.String(20), nullable=False),
            sa.UniqueConstraint("plan_id", "student_id", name="uq_exam_plan_student"),
        )
        op.create_index("ix_exam_plan_students_plan_id", "exam_plan_students", ["plan_id"])
    if "settings" not in existing_columns(bind, "exam_versions"):
        op.add_column("exam_versions", sa.Column("settings", JSONType))
    if not nullable(bind, "exam_version_papers", "source_provider_id"):
        # The append-only trigger only fires on row UPDATE/DELETE, so relaxing the column is allowed.
        op.alter_column("exam_version_papers", "source_provider_id", existing_type=sa.Uuid(), nullable=True)
    # Re-install the append-only triggers so exam_versions.settings is protected like the rest of the manifest.
    ensure_triggers(bind, "exam")


def _create_global_tests() -> None:
    op.create_table(
        "global_tests",
        sa.Column("test_id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("exam_type", sa.String(80)),
        sa.Column("target_class", sa.String(80)),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("derived_from_test_id", sa.Uuid(), sa.ForeignKey("global_tests.test_id")),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("published_by", sa.Uuid()),
        sa.Column("published_at", TS),
        sa.Column("withdrawn_at", TS),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','WITHDRAWN')", name="global_tests_status_check"),
        sa.CheckConstraint("duration_minutes > 0 AND duration_minutes <= 720", name="global_tests_duration_check"),
    )
    op.create_table(
        "global_test_sections",
        sa.Column("section_id", sa.Uuid(), primary_key=True),
        sa.Column("test_id", sa.Uuid(), sa.ForeignKey("global_tests.test_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id"), nullable=False),
        sa.UniqueConstraint("test_id", "position", name="uq_global_test_section_position"),
        sa.UniqueConstraint("test_id", "subject_id", name="uq_global_test_section_subject"),
    )
    op.create_index("ix_global_test_sections_test_id", "global_test_sections", ["test_id"])


def _alter_plans(bind) -> None:
    have = existing_columns(bind, "exam_plans")
    for col in (
        sa.Column("audience_type", sa.String(12), nullable=False, server_default="BATCHES"),
        sa.Column("marking_mode", sa.String(12), nullable=False, server_default="PER_QUESTION"),
        sa.Column("marks_correct", sa.Integer()),
        sa.Column("marks_wrong", sa.Integer()),
        sa.Column("exam_type", sa.String(80)),
        sa.Column("target_class", sa.String(80)),
        sa.Column("global_test_id", sa.Uuid(), sa.ForeignKey("global_tests.test_id")),
    ):
        if col.name not in have:
            op.add_column("exam_plans", col)
    if "ix_exam_plans_global_test_id" not in {i["name"] for i in sa.inspect(bind).get_indexes("exam_plans")}:
        op.create_index("ix_exam_plans_global_test_id", "exam_plans", ["global_test_id"])
    checks = existing_checks(bind, "exam_plans")
    with op.batch_alter_table("exam_plans") as b:
        if "exam_plans_status_check" in checks:
            b.drop_constraint("exam_plans_status_check", type_="check")
        b.create_check_constraint("exam_plans_status_check", "status IN ('DRAFT','ACTIVE','INACTIVE')")
        if "exam_plans_audience_check" not in checks:
            b.create_check_constraint("exam_plans_audience_check", "audience_type IN ('BATCHES', 'BRANCH', 'INSTITUTE', 'STUDENTS')")
        if "exam_plans_marking_check" not in checks:
            b.create_check_constraint("exam_plans_marking_check", "marking_mode IN ('PER_QUESTION','UNIFORM')")
        if "exam_plans_uniform_marks_check" not in checks:
            b.create_check_constraint("exam_plans_uniform_marks_check", "marking_mode = 'PER_QUESTION' OR (marks_correct > 0 AND marks_wrong >= 0)")


def downgrade() -> None:
    # Forward-only by policy (exam history).
    pass
