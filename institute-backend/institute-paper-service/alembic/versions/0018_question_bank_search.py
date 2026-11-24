"""Indexes for the question bank search

Additive only: builds indexes without blocking writes; no table or row changes. Each one serves the query named beside it.
The trigram index needs the pg_trgm extension (created if missing; the downgrade leaves it, other objects may use it).

Revision ID: 0018_question_bank_search
Revises: 0017_taxonomy_aliases
"""
from alembic import op

revision = "0018_question_bank_search"
down_revision = "0017_taxonomy_aliases"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_bank_questions_created", "bank_questions (created_at, question_id)"),                    # bank list, newest first
    ("ix_question_versions_stem_trgm", "question_versions USING gin (stem public.gin_trgm_ops)"),  # bank text search (ILIKE '%q%')
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
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
