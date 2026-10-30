"""Global Test paper choice: one exam per occurrence (additive)

  * global_test_student_subjects: status SELECTABLE (two or more eligible papers; the student chooses one), with no submission_id, so
    the existing PAPER <=> submission_id rule holds unchanged. Every existing status and row stays valid.
  * global_test_student_subject_papers: every eligible paper of a student's PAPER or SELECTABLE subject (submission, version, provider),
    frozen at generation.
  * global_test_occurrences.generation_attempts / generation_failed_at: bounded generation retries; a failed occurrence waits for a
    Super Admin's manual retry. Existing occurrences start at 0 / NULL.
Nothing existing is dropped or rewritten.

Revision ID: 0010_global_paper_choice
Revises: 0009_global_test_students
"""
import sqlalchemy as sa
from alembic import op

from institute_common.migrations import existing_columns

revision = "0010_global_paper_choice"
down_revision = "0009_global_test_students"
branch_labels = None
depends_on = None

OLD_STATUSES = "('PAPER','NOT_ENROLLED','NO_SUBMISSION','NOT_APPROVED','NOT_COVERED','CONFLICT')"
NEW_STATUSES = "('PAPER','SELECTABLE','NOT_ENROLLED','NO_SUBMISSION','NOT_APPROVED','NOT_COVERED','CONFLICT')"


def upgrade() -> None:
    bind = op.get_bind()
    columns = existing_columns(bind, "global_test_occurrences")
    with op.batch_alter_table("global_test_occurrences") as b:
        if "generation_attempts" not in columns:
            b.add_column(sa.Column("generation_attempts", sa.Integer(), nullable=False, server_default="0"))
        if "generation_failed_at" not in columns:
            b.add_column(sa.Column("generation_failed_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("global_test_student_subjects") as b:
        b.drop_constraint("global_test_student_subjects_status_check", type_="check")
        b.create_check_constraint("global_test_student_subjects_status_check", f"status IN {NEW_STATUSES}")
    if "global_test_student_subject_papers" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "global_test_student_subject_papers",
            sa.Column("occurrence_id", sa.Uuid(), nullable=False),
            sa.Column("student_id", sa.String(20), nullable=False),
            sa.Column("subject_id", sa.Uuid(), nullable=False),
            sa.Column("submission_id", sa.Uuid(), sa.ForeignKey("global_paper_submissions.submission_id"), nullable=False),
            sa.Column("paper_version_id", sa.Uuid(), nullable=False),
            sa.Column("provider_id", sa.Uuid(), nullable=False),
            sa.PrimaryKeyConstraint("occurrence_id", "student_id", "subject_id", "submission_id", name="pk_global_test_student_subject_papers"),
            sa.ForeignKeyConstraint(["occurrence_id", "student_id", "subject_id"],
                                    ["global_test_student_subjects.occurrence_id", "global_test_student_subjects.student_id",
                                     "global_test_student_subjects.subject_id"], name="fk_global_test_student_subject_papers_subject"),
        )


def downgrade() -> None:
    op.drop_table("global_test_student_subject_papers")
    with op.batch_alter_table("global_test_student_subjects") as b:
        b.drop_constraint("global_test_student_subjects_status_check", type_="check")
        b.create_check_constraint("global_test_student_subjects_status_check", f"status IN {OLD_STATUSES}")
    with op.batch_alter_table("global_test_occurrences") as b:
        b.drop_column("generation_failed_at")
        b.drop_column("generation_attempts")
