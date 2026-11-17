"""Index for the results of a student

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: d9b3f5a1c7e2
Revises: c6f1d9a3b5e8
"""
from alembic import op

revision = "d9b3f5a1c7e2"
down_revision = "c6f1d9a3b5e8"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_test_results_student", "test_results (student_id)"),                      # Results page and performance
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
