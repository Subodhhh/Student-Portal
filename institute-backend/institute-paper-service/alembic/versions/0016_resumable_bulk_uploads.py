"""Resumable bulk uploads and bulk-upload notifications (additive)

  * bulk_import_uploads: a file being sent in pieces (resume after an interrupted transfer); its pieces live in the object store.
  * paper_notification_outbox: staff notifications of the paper service (bulk upload preview ready / import finished), delivered to
    the operations service's inbox by the bulk-import worker - the exam service's outbox pattern.
  * bulk_import_jobs.source_size INTEGER -> BIGINT: uploads may now reach 2 GiB (an INTEGER holds at most 2 GiB - 1 byte).

Existing rows keep every value (widening an integer column keeps them). Downgrade refuses while an upload session exists or a job's
file is too large for INTEGER. Idempotent for a database whose baseline already has these shapes.

Revision ID: 0016_resumable_bulk_uploads
Revises: 0015_question_types
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType

revision = "0016_resumable_bulk_uploads"
down_revision = "0015_question_types"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    op.execute("ALTER TABLE bulk_import_jobs ALTER COLUMN source_size TYPE BIGINT")
    if "bulk_import_uploads" not in tables:
        op.create_table(
            "bulk_import_uploads",
            sa.Column("upload_id", sa.Uuid(), primary_key=True),
            sa.Column("scope", sa.String(10), nullable=False),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id")),
            sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
            sa.Column("file_format", sa.String(4), nullable=False),
            sa.Column("file_name", sa.String(255), nullable=False),
            sa.Column("fingerprint", sa.String(300), nullable=False),
            sa.Column("size", sa.BigInteger(), nullable=False),
            sa.Column("chunk_size", sa.Integer(), nullable=False),
            sa.Column("received_parts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("received_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(10), nullable=False, server_default="RECEIVING"),
            sa.Column("job_id", sa.Uuid(), sa.ForeignKey("bulk_import_jobs.job_id")),
            sa.Column("created_by", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.CheckConstraint("status IN ('RECEIVING', 'COMPLETED', 'DISCARDED')", name="bulk_import_uploads_status_check"),
            sa.CheckConstraint("file_format IN ('XLSX','DOCX')", name="bulk_import_uploads_format_check"),
            sa.CheckConstraint("(scope = 'GLOBAL' AND provider_id IS NULL) OR (scope = 'PROVIDER' AND provider_id IS NOT NULL)",
                               name="bulk_import_uploads_target_check"),
            sa.CheckConstraint("received_bytes >= 0 AND received_bytes <= size AND received_parts >= 0 AND chunk_size > 0 AND size > 0",
                               name="bulk_import_uploads_progress_check"),
        )
        op.create_index("ix_bulk_import_uploads_owner_status", "bulk_import_uploads", ["created_by", "status"])
        op.create_index("ix_bulk_import_uploads_status_updated", "bulk_import_uploads", ["status", "updated_at"])
    if "paper_notification_outbox" not in tables:
        op.create_table(
            "paper_notification_outbox",
            sa.Column("outbox_id", sa.Uuid(), primary_key=True),
            sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
            sa.Column("payload", JSONType, nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("last_error", sa.String(300)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("sent_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("status IN ('PENDING','SENT','FAILED')", name="paper_notification_outbox_status_check"),
        )
        op.create_index("ix_paper_notification_outbox_due", "paper_notification_outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    bind = op.get_bind()
    sessions = bind.execute(sa.text("SELECT count(*) FROM bulk_import_uploads")).scalar()
    large = bind.execute(sa.text("SELECT count(*) FROM bulk_import_jobs WHERE source_size > 2147483647")).scalar()
    if sessions or large:
        raise RuntimeError(f"{sessions} resumable upload(s) and {large} job(s) with a file over 2 GiB exist; they cannot be represented "
                           "before this revision. Downgrade refused.")
    op.drop_table("paper_notification_outbox")
    op.drop_table("bulk_import_uploads")
    op.execute("ALTER TABLE bulk_import_jobs ALTER COLUMN source_size TYPE INTEGER")
