"""Global Test contributions: idempotency record of authorized provider-to-platform paper copies (additive)

  * global_paper_copies: one row per approved contribution (unique contribution_id) naming the exact source version and the GLOBAL
    paper/version made from it, so a retried copy returns the same platform paper instead of creating another.
A fresh database gets the table from the baseline (current models); this revision creates it only where it is missing.

Revision ID: 0003_global_paper_copies
Revises: 0002_paper_question_bank
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_global_paper_copies"
down_revision = "0002_paper_question_bank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "global_paper_copies" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "global_paper_copies",
        sa.Column("copy_id", sa.Uuid(), primary_key=True),
        sa.Column("contribution_id", sa.Uuid(), nullable=False),
        sa.Column("source_paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id"), nullable=False),
        sa.Column("global_paper_id", sa.Uuid(), sa.ForeignKey("papers.paper_id"), nullable=False),
        sa.Column("global_paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id"), nullable=False),
        sa.Column("authorization_reference", sa.String(200), nullable=False),
        sa.Column("copied_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("contribution_id", name="uq_global_paper_copy_contribution"),
    )


def downgrade() -> None:
    # Forward-only by policy: copies are referenced by contributions and platform papers.
    pass
