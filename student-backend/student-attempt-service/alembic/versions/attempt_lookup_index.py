"""Index for attempt lookups by student

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: attempt_lookup_index
Revises: attempt_paper_selections
"""
from alembic import op

revision = "attempt_lookup_index"
down_revision = "attempt_paper_selections"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_test_attempts_student_test", "test_attempts (student_id, test_id)"),      # completed attempts, missed tests, attempt of a test (uq_test_attempts_student_test skips retakes)
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
