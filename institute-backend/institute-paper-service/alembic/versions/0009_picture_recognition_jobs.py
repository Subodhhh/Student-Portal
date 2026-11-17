"""Picture recognition jobs (additive)

One new table, picture_recognition_jobs: one recogniser's work on one picture, claimed by recognition workers (CPU or GPU pools).
Nothing existing is changed. A database created after the table joined the models already has it (0001 baseline) and is adopted when it
has exactly these columns.

Revision ID: 0009_picture_recognition_jobs
Revises: 0008_question_images
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType

revision = "0009_picture_recognition_jobs"
down_revision = "0008_question_images"
branch_labels = None
depends_on = None

TABLE = "picture_recognition_jobs"


def _columns() -> list[sa.Column]:
    return [
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("question_image_assets.asset_id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("recognizer", sa.String(40), nullable=False),
        sa.Column("version", sa.String(120), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="QUEUED"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("worker", sa.String(120)),
        sa.Column("result", JSONType),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    ]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        want, have = {c.name for c in _columns()}, {c["name"] for c in inspector.get_columns(TABLE)}
        if want != have:
            raise RuntimeError(f"{TABLE} already exists but differs from what 0009 creates (missing {sorted(want - have)}, "
                               f"unexpected {sorted(have - want)}). Fix the table by hand before upgrading.")
        return
    op.create_table(
        TABLE, *_columns(),
        sa.UniqueConstraint("asset_id", "recognizer", "version", name="uq_picture_recognition_jobs_asset_recognizer_version"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','DONE','FAILED')", name="picture_recognition_jobs_status_check"),
    )
    op.create_index("ix_picture_recognition_jobs_claim", TABLE, ["recognizer", "status", "locked_until"])
    op.create_index("ix_picture_recognition_jobs_asset_id", TABLE, ["asset_id"])


def downgrade() -> None:
    op.drop_table(TABLE)
