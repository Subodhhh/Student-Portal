"""Index for the test audience checks

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: a3f1c7d9e2b4
Revises: f2a7c4e9b1d3
"""
from alembic import op

revision = "a3f1c7d9e2b4"
down_revision = "f2a7c4e9b1d3"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_test_audiences_lookup", "test_audiences (audience_type, student_id, test_id)"), # every test list, details and start: who may see a test
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
