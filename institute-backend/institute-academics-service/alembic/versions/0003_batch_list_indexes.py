"""Indexes for batch and teaching lists

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: 0003_batch_list_indexes
Revises: 0002_batch_academic_defaults
"""
from alembic import op

revision = "0003_batch_list_indexes"
down_revision = "0002_batch_academic_defaults"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_batches_branch_name", "batches (branch_id, name)"),                       # batches of a branch, by name
    ("ix_tsa_branch", "teacher_subject_assignments (branch_id)"),                  # teachers of a branch
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
