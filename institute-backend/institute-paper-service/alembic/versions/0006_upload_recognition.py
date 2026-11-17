"""Scientific-notation recognition record of each paper upload (additive)

One nullable JSONB column, paper_uploads.recognition: for every imported field, the provider's original text, the stored text, the
recognition outcome and reasons, the provider's review decision and the engine version. Existing rows stay NULL; nothing is rewritten.
Adding a nullable column without a default is a metadata-only change in PostgreSQL (no table rewrite, brief lock only).

A database created after this column joined the models already has it: 0001_paper_baseline creates missing tables from the current
models. As in 0002, the column is then not added again - but only when it is exactly what this migration creates (same type, nullable,
no default); any other shape stops the upgrade instead of being silently accepted.

Revision ID: 0006_upload_recognition
Revises: 0005_paper_list_indexes
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType

revision = "0006_upload_recognition"
down_revision = "0005_paper_list_indexes"
branch_labels = None
depends_on = None


def _column() -> sa.Column:
    return sa.Column("recognition", JSONType, nullable=True)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"]: c for c in sa.inspect(bind).get_columns("paper_uploads")}
    if "recognition" not in existing:
        op.add_column("paper_uploads", _column())
        return
    have = existing["recognition"]
    want_type = _column().type.compile(dialect=bind.dialect)
    have_type = have["type"].compile(dialect=bind.dialect)
    problems = []
    if have_type.upper() != want_type.upper():
        problems.append(f"type {have_type} (expected {want_type})")
    if not have["nullable"]:
        problems.append("NOT NULL (expected nullable)")
    if have.get("default") is not None:
        problems.append(f"default {have['default']} (expected none)")
    if problems:
        raise RuntimeError("paper_uploads.recognition already exists but differs from what 0006_upload_recognition creates: "
                           + "; ".join(problems) + ". Fix the column by hand before upgrading.")


def downgrade() -> None:
    op.drop_column("paper_uploads", "recognition")
