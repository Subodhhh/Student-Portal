"""Recognition review drafts saved as they are made (additive)

One new table, paper_review_drafts: an import review (with its stored question file and answer key) or a correction in progress,
its decisions per field with the revision that last changed each one (no silent overwrite between two editors), the engine version,
and who changed it, when and from where. At most one OPEN draft per person per file (IMPORT) or per paper version (CORRECTION):
partial unique indexes. Nothing existing is changed.

A database created after this table joined the models already has it: 0001_paper_baseline creates missing tables from the current
models. It is then adopted only when it has exactly the columns and the two partial unique indexes this migration creates; any other
shape stops the upgrade instead of being silently accepted.

Revision ID: 0007_paper_review_drafts
Revises: 0006_upload_recognition
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import IPAddress, JSONType

revision = "0007_paper_review_drafts"
down_revision = "0006_upload_recognition"
branch_labels = None
depends_on = None

TABLE = "paper_review_drafts"
NOW = sa.text("CURRENT_TIMESTAMP")
OPEN_IMPORT = "status = 'OPEN' AND kind = 'IMPORT'"
OPEN_CORRECTION = "status = 'OPEN' AND kind = 'CORRECTION'"
UNIQUE = {"ux_paper_review_drafts_open_import", "ux_paper_review_drafts_open_correction"}


def _columns() -> list[sa.Column]:
    return [
        sa.Column("draft_id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("paper_id", sa.Uuid(), sa.ForeignKey("papers.paper_id")),
        sa.Column("paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id")),
        sa.Column("title", sa.String(255)),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("stored_files.file_id")),
        sa.Column("file_name", sa.String(255)),
        sa.Column("file_ext", sa.String(10)),
        sa.Column("answer_key_file_id", sa.Uuid(), sa.ForeignKey("stored_files.file_id")),
        sa.Column("review_token", sa.String(64)),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("decisions", JSONType, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(12), nullable=False, server_default="OPEN"),
        sa.Column("result_upload_id", sa.Uuid(), sa.ForeignKey("paper_uploads.upload_id")),
        sa.Column("result_paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id")),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_ip", IPAddress),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_ip", IPAddress),
        sa.Column("last_opened_at", sa.DateTime(timezone=True)),
    ]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        want = {c.name for c in _columns()}
        have = {c["name"] for c in inspector.get_columns(TABLE)}
        unique = {i["name"] for i in inspector.get_indexes(TABLE) if i.get("unique")}
        problems = ([f"columns differ (missing {sorted(want - have)}, unexpected {sorted(have - want)})"] if have != want else []) + \
                   ([f"missing unique indexes {sorted(UNIQUE - unique)}"] if UNIQUE - unique else [])
        if problems:
            raise RuntimeError(f"{TABLE} already exists but differs from what 0007_paper_review_drafts creates: " + "; ".join(problems)
                               + ". Fix the table by hand before upgrading.")
        return
    op.create_table(
        TABLE, *_columns(),
        sa.CheckConstraint("kind IN ('IMPORT','CORRECTION')", name="paper_review_drafts_kind_check"),
        sa.CheckConstraint("status IN ('OPEN','IMPORTED','DISCARDED')", name="paper_review_drafts_status_check"),
        sa.CheckConstraint("(kind = 'IMPORT' AND file_id IS NOT NULL AND review_token IS NOT NULL AND file_ext IS NOT NULL) OR "
                           "(kind = 'CORRECTION' AND paper_version_id IS NOT NULL)", name="paper_review_drafts_kind_columns_check"),
        sa.CheckConstraint("revision >= 1", name="paper_review_drafts_revision_check"),
    )
    op.create_index("ux_paper_review_drafts_open_import", TABLE,
                    ["provider_id", "branch_id", "subject_id", "review_token", "created_by"], unique=True,
                    postgresql_where=sa.text(OPEN_IMPORT), sqlite_where=sa.text(OPEN_IMPORT))
    op.create_index("ux_paper_review_drafts_open_correction", TABLE,
                    ["provider_id", "branch_id", "subject_id", "paper_version_id", "created_by"], unique=True,
                    postgresql_where=sa.text(OPEN_CORRECTION), sqlite_where=sa.text(OPEN_CORRECTION))
    op.create_index("ix_paper_review_drafts_branch_status", TABLE, ["branch_id", "status", "updated_at"])


def downgrade() -> None:
    # the drafts are work in progress, never a paper: the stored files they reference stay (stored_files is append-only)
    op.drop_table(TABLE)
