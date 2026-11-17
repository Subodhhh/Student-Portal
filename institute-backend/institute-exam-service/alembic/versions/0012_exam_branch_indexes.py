"""Indexes for the exam lists of a branch

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: 0012_exam_branch_indexes
Revises: 0011_shuffle_questions
"""
from alembic import op

revision = "0012_exam_branch_indexes"
down_revision = "0011_shuffle_questions"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_exams_branch", "exams (branch_id)"),                                      # exams of a branch
    ("ix_exam_plans_branch", "exam_plans (branch_id)"),                            # exam plans of a branch
)


def upgrade() -> None:
    # A pooled connection can have an empty search_path and CONCURRENTLY runs outside the transaction that sets it: names are qualified.
    with op.get_context().autocommit_block():
        for name, target in INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON public.{target}")
    op.execute("SET LOCAL search_path TO public")    # the block ended the transaction that set it (pooled connections), and the version row follows


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _target in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
    op.execute("SET LOCAL search_path TO public")
