"""Unified Global Test exams: per-subject provenance (additive)

  * exam_plan_subjects.source_submission_id (nullable): in a unified Global Test plan, the provider submission a subject's paper
    comes from; generation authorizes the paper through it instead of the plan's provider.
  * exam_version_papers.source_submission_id (nullable): the same provenance frozen into the generated manifest; staff access to a
    unified exam is derived from it (each provider sees only its own subjects and students).
Existing rows stay NULL, so every exam generated before this revision keeps its behaviour. Nothing is dropped or rewritten.

Revision ID: 0008_unified_global_exams
Revises: 0007_global_test_schedule
"""
import sqlalchemy as sa
from alembic import op

from institute_common.migrations import existing_columns

revision = "0008_unified_global_exams"
down_revision = "0007_global_test_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "source_submission_id" not in existing_columns(bind, "exam_plan_subjects"):
        with op.batch_alter_table("exam_plan_subjects") as b:
            b.add_column(sa.Column("source_submission_id", sa.Uuid(), nullable=True))
            b.create_foreign_key("exam_plan_subjects_source_submission_id_fkey", "global_paper_submissions",
                                 ["source_submission_id"], ["submission_id"])
    if "source_submission_id" not in existing_columns(bind, "exam_version_papers"):
        with op.batch_alter_table("exam_version_papers") as b:
            b.add_column(sa.Column("source_submission_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("exam_version_papers") as b:
        b.drop_column("source_submission_id")
    with op.batch_alter_table("exam_plan_subjects") as b:
        b.drop_constraint("exam_plan_subjects_source_submission_id_fkey", type_="foreignkey")
        b.drop_column("source_submission_id")
