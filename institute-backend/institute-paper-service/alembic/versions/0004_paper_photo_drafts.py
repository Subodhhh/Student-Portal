"""Photo papers: drafts built from mobile photos with a separate answer key, previewed and confirmed before import (additive)

  * paper_photo_drafts: the draft (title, branch, subject), the OCR result as read (`extracted`), the corrected questions, issues,
    the correction log and, once accepted, the paper version it became.
  * paper_photo_draft_files: its page photos and answer-key photos/file in order (stored_files), with each file's OCR lines.
A fresh database gets these from the baseline (current models); this revision creates them only where they are missing.

Revision ID: 0004_paper_photo_drafts
Revises: 0003_global_paper_copies
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType

revision = "0004_paper_photo_drafts"
down_revision = "0003_global_paper_copies"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "paper_photo_drafts" not in tables:
        op.create_table(
            "paper_photo_drafts",
            sa.Column("draft_id", sa.Uuid(), primary_key=True),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
            sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
            sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
            sa.Column("client_request_id", sa.String(100), nullable=False),
            sa.Column("questions", JSONType),
            sa.Column("extracted", JSONType),
            sa.Column("issues", JSONType),
            sa.Column("corrections", JSONType),
            sa.Column("extracted_at", sa.DateTime(timezone=True)),
            sa.Column("accepted_paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id")),
            sa.Column("accepted_at", sa.DateTime(timezone=True)),
            sa.Column("created_by", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.UniqueConstraint("client_request_id", name="paper_photo_drafts_client_request_id_key"),
            sa.CheckConstraint("status IN ('DRAFT','ACCEPTED','DISCARDED')", name="paper_photo_drafts_status_check"),
        )
        op.create_index("ix_paper_photo_drafts_provider_id", "paper_photo_drafts", ["provider_id"])
    if "paper_photo_draft_files" not in tables:
        op.create_table(
            "paper_photo_draft_files",
            sa.Column("draft_file_id", sa.Uuid(), primary_key=True),
            sa.Column("draft_id", sa.Uuid(), sa.ForeignKey("paper_photo_drafts.draft_id"), nullable=False),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("file_id", sa.Uuid(), sa.ForeignKey("stored_files.file_id"), nullable=False),
            sa.Column("ocr_lines", JSONType),
            sa.Column("ocr_error", sa.String(500)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.UniqueConstraint("draft_id", "kind", "position", name="uq_photo_draft_file_position"),
            sa.CheckConstraint("kind IN ('PAGE','ANSWER_KEY')", name="paper_photo_draft_files_kind_check"),
        )
        op.create_index("ix_paper_photo_draft_files_draft_id", "paper_photo_draft_files", ["draft_id"])


def downgrade() -> None:
    # Forward-only by policy: drafts reference the paper versions they became.
    pass
