"""Indexes for enrollment and batch membership lookups

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: c4e8a2b7d1f6
Revises: b852c65e7013
"""
from alembic import op

revision = "c4e8a2b7d1f6"
down_revision = "b852c65e7013"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_enrollments_student_id", "enrollments (student_id)"),                     # My Institutes and every test eligibility check
    ("ix_enrollment_batches_enrollment", "enrollment_batches (enrollment_id)"),    # batch membership of a student
    ("ix_enrollment_batches_batch", "enrollment_batches (batch_id)"),              # reference sync and audience joins
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
