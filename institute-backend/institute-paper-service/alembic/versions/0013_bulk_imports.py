"""Bulk question import (additive)

Three new tables, nothing existing is changed:
  * bulk_import_jobs: one uploaded Excel/Word file (status, counts, worker lease, who uploaded/confirmed it, when and from where);
  * bulk_import_rows: one question of the file as read and corrected, with its validation result (a preview, not the bank);
  * bulk_import_images: the distinct pictures of a job, staged in the object store until imported.
A database created after these joined the models already has them (0001 baseline); they are then adopted only when they have
exactly these columns; any other shape stops the upgrade.

Revision ID: 0013_bulk_imports
Revises: 0012_object_storage_keys
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import IPAddress, JSONType

revision = "0013_bulk_imports"
down_revision = "0012_object_storage_keys"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")
JOB_STATUSES = ("UPLOADED", "PARSING", "VALIDATE_QUEUED", "VALIDATING", "PREVIEW", "IMPORT_QUEUED", "IMPORTING",
                "COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "EXPIRED")
ROW_STATUSES = ("PENDING", "VALID", "INVALID", "DUPLICATE", "IMPORTED", "FAILED")
LATER_COLUMNS = {"bulk_import_jobs": {"scope"}}     # 0014_three_scope_question_bank


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _count(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=False, server_default="0")


def _jobs() -> list:
    return [
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="UPLOADED"),
        sa.Column("file_format", sa.String(4), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(300)),
        sa.Column("source_size", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        _count("total_rows"), _count("valid_rows"), _count("invalid_rows"), _count("duplicate_rows"), _count("imported_rows"),
        _count("failed_rows"),
        sa.Column("file_errors", JSONType),
        sa.Column("taxonomy_mappings", JSONType),
        _count("attempts"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("worker", sa.String(120)),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_ip", IPAddress),
        sa.Column("confirmed_by", sa.Uuid()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_ip", IPAddress),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cleaned_at", sa.DateTime(timezone=True)),
    ]


def _rows() -> list:
    return [
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("bulk_import_jobs.job_id"), primary_key=True),
        sa.Column("row_no", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("data", JSONType, nullable=False),
        sa.Column("images", JSONType),
        sa.Column("read_errors", JSONType),
        sa.Column("errors", JSONType),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("chapter_id", sa.Uuid()),
        sa.Column("topic_id", sa.Uuid()),
        sa.Column("subtopic_id", sa.Uuid()),
        sa.Column("question_id", sa.Uuid()),
        sa.Column("edited_by", sa.Uuid()),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
    ]


def _images() -> list:
    return [
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("bulk_import_jobs.job_id"), primary_key=True),
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("staging_key", sa.String(300), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
    ]


def _create_jobs() -> None:
    op.create_table("bulk_import_jobs", *_jobs(),
                    sa.CheckConstraint(f"status IN ({_in(JOB_STATUSES)})", name="bulk_import_jobs_status_check"),
                    sa.CheckConstraint("file_format IN ('XLSX','DOCX')", name="bulk_import_jobs_format_check"))
    op.create_index("ix_bulk_import_jobs_provider_id", "bulk_import_jobs", ["provider_id"])
    op.create_index("ix_bulk_import_jobs_branch_created", "bulk_import_jobs", ["branch_id", "created_at"])
    op.create_index("ix_bulk_import_jobs_claim", "bulk_import_jobs", ["status", "locked_until"])


def _create_rows() -> None:
    op.create_table("bulk_import_rows", *_rows(),
                    sa.CheckConstraint(f"status IN ({_in(ROW_STATUSES)})", name="bulk_import_rows_status_check"))
    op.create_index("ix_bulk_import_rows_job_status", "bulk_import_rows", ["job_id", "status", "row_no"])
    op.create_index("ix_bulk_import_rows_job_hash", "bulk_import_rows", ["job_id", "content_hash"])


def _create_images() -> None:
    op.create_table("bulk_import_images", *_images())


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, columns, create in (("bulk_import_jobs", _jobs(), _create_jobs), ("bulk_import_rows", _rows(), _create_rows),
                                   ("bulk_import_images", _images(), _create_images)):
        if table not in inspector.get_table_names():
            create()
            continue
        # columns a LATER revision adds are expected on a table the baseline created from today's models
        want, have = {c.name for c in columns}, {c["name"] for c in inspector.get_columns(table)} - LATER_COLUMNS.get(table, set())
        if want != have:
            raise RuntimeError(f"{table} already exists but differs from what 0013_bulk_imports creates (missing {sorted(want - have)}, "
                               f"unexpected {sorted(have - want)}). Fix the table by hand before upgrading.")


def downgrade() -> None:
    # Refuse while imports exist: their history, reports and staged pictures would be lost.
    jobs = op.get_bind().execute(sa.text("SELECT count(*) FROM bulk_import_jobs")).scalar()
    if jobs:
        raise RuntimeError(f"{jobs} bulk import job(s) exist; downgrading would lose them. Downgrade refused.")
    op.drop_table("bulk_import_images")
    op.drop_table("bulk_import_rows")
    op.drop_table("bulk_import_jobs")
