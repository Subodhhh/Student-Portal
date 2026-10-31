"""Shuffle Questions for Daily and Weekly Tests (additive)

  * exam_plans.shuffle_questions, test_series.shuffle_questions: NOT NULL, default false. Every existing plan and Weekly Test keeps
    false, i.e. today's paper order; generated exams record the choice in exam_versions.settings (no column there).
Nothing existing is dropped or rewritten.

Revision ID: 0011_shuffle_questions
Revises: 0010_global_paper_choice
"""
import sqlalchemy as sa
from alembic import op

from institute_common.migrations import existing_columns

revision = "0011_shuffle_questions"
down_revision = "0010_global_paper_choice"
branch_labels = None
depends_on = None

TABLES = ("exam_plans", "test_series")


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        if "shuffle_questions" not in existing_columns(bind, table):
            with op.batch_alter_table(table) as b:
                b.add_column(sa.Column("shuffle_questions", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        if "shuffle_questions" in existing_columns(bind, table):
            with op.batch_alter_table(table) as b:
                b.drop_column("shuffle_questions")
