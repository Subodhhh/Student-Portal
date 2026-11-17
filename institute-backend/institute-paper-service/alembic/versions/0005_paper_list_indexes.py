"""Indexes for the paper, upload and question-bank lists

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.

Revision ID: 0005_paper_list_indexes
Revises: 0004_paper_photo_drafts
"""
from alembic import op

revision = "0005_paper_list_indexes"
down_revision = "0004_paper_photo_drafts"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_papers_branch_created", "papers (branch_id, created_at)"),                # Papers list of one branch, newest first
    ("ix_paper_uploads_branch_created", "paper_uploads (branch_id, created_at)"),  # Import Processing history
    ("ix_bank_questions_branch_id", "bank_questions (branch_id)"),                 # Question Bank of one branch
    ("ix_pvq_question_version", "paper_version_questions (question_version_id)"),  # "used in N papers" and reserved-question checks
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
