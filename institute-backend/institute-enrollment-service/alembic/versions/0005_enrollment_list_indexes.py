"""Indexes for rosters and batch membership

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: 0005_enrollment_list_indexes
Revises: 0004_enrollment_academics
"""
from alembic import op

revision = "0005_enrollment_list_indexes"
down_revision = "0004_enrollment_academics"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_enrollments_branch_status", "enrollments (branch_id, status)"),           # branch roster, dashboards
    ("ix_enrollments_provider_status", "enrollments (provider_id, status)"),       # provider student directory and counts
    ("ix_enrollment_batches_enrollment", "enrollment_batches (enrollment_id)"),    # batches of each listed enrollment
    ("ix_enrollment_batches_batch", "enrollment_batches (batch_id)"),              # members of a batch, student counts
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
