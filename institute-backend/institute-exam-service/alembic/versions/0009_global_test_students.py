"""Unified Global Test exams: one row per eligible student and date, and every configured subject's outcome (additive)

  * global_test_students: PRIMARY KEY (occurrence_id, student_id), so a student can never hold two unified exams for one date; plan and
    exam the student was given, and a reference enrollment.
  * global_test_student_subjects: for every eligible student, one row per CONFIGURED subject of the test (configured position), with the
    selected paper (status PAPER: submission, version, provider) or the reason there is none (NOT_ENROLLED, NO_SUBMISSION, NOT_APPROVED,
    NOT_COVERED, CONFLICT). The student portal shows every configured subject from it, in order, with that reason.
New tables only: nothing existing is altered, so dates generated before this revision keep their behaviour.

Revision ID: 0009_global_test_students
Revises: 0008_unified_global_exams
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType

revision = "0009_global_test_students"
down_revision = "0008_unified_global_exams"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")
STATUSES = "('PAPER','NOT_ENROLLED','NO_SUBMISSION','NOT_APPROVED','NOT_COVERED','CONFLICT')"


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "global_test_students" not in tables:
        op.create_table(
            "global_test_students",
            sa.Column("occurrence_id", sa.Uuid(), sa.ForeignKey("global_test_occurrences.occurrence_id"), nullable=False),
            sa.Column("student_id", sa.String(20), nullable=False),
            sa.Column("enrollment_id", sa.String(64)),
            sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("exam_plans.plan_id")),
            sa.Column("exam_id", sa.Uuid(), sa.ForeignKey("exams.exam_id")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.PrimaryKeyConstraint("occurrence_id", "student_id", name="pk_global_test_students"),
        )
        op.create_index("ix_global_test_students_plan", "global_test_students", ["plan_id"])
    if "global_test_student_subjects" not in tables:
        op.create_table(
            "global_test_student_subjects",
            sa.Column("occurrence_id", sa.Uuid(), nullable=False),
            sa.Column("student_id", sa.String(20), nullable=False),
            sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("submission_id", sa.Uuid(), sa.ForeignKey("global_paper_submissions.submission_id")),
            sa.Column("paper_version_id", sa.Uuid()),
            sa.Column("provider_id", sa.Uuid()),
            sa.Column("detail", JSONType),
            sa.PrimaryKeyConstraint("occurrence_id", "student_id", "subject_id", name="pk_global_test_student_subjects"),
            sa.ForeignKeyConstraint(["occurrence_id", "student_id"], ["global_test_students.occurrence_id", "global_test_students.student_id"],
                                    name="fk_global_test_student_subjects_student"),
            sa.CheckConstraint(f"status IN {STATUSES}", name="global_test_student_subjects_status_check"),
            sa.CheckConstraint("(status = 'PAPER') = (submission_id IS NOT NULL)", name="global_test_student_subjects_paper_check"),
        )


def downgrade() -> None:
    op.drop_table("global_test_student_subjects")
    op.drop_index("ix_global_test_students_plan", table_name="global_test_students")
    op.drop_table("global_test_students")
