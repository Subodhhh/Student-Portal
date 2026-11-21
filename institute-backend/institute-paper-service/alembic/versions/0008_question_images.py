"""Pictures for questions and options (additive)

* New table question_image_assets: each picture stored once per provider (unique provider_id + SHA-256 checksum), its bytes in
  image_data (BYTEA) with `storage` = 'db', plus name, MIME type, size, dimensions, who uploaded it, when and from where.
* question_versions gains five nullable references: stem_image_id and option_a_image_id .. option_d_image_id.

Existing rows are not touched (the new columns are NULL; adding a nullable column without a default does not rewrite the table in
PostgreSQL). A database created after these joined the models already has them (0001_paper_baseline creates the current models); they
are then adopted only when they are exactly what this migration creates; any other shape stops the upgrade.

Revision ID: 0008_question_images
Revises: 0007_paper_review_drafts
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import IPAddress

revision = "0008_question_images"
down_revision = "0007_paper_review_drafts"
branch_labels = None
depends_on = None

TABLE = "question_image_assets"
NOW = sa.text("CURRENT_TIMESTAMP")
LATER_COLUMNS = {"storage_key"}          # 0012_object_storage_keys
IMAGE_COLUMNS = ("stem_image_id", "option_a_image_id", "option_b_image_id", "option_c_image_id", "option_d_image_id")


def _columns() -> list[sa.Column]:
    return [
        sa.Column("asset_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("storage", sa.String(10), nullable=False, server_default="db"),
        sa.Column("image_data", sa.LargeBinary()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_ip", IPAddress),
    ]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        want = {c.name for c in _columns()}
        # columns a LATER revision adds are expected on a table the baseline created from today's models
        have = {c["name"] for c in inspector.get_columns(TABLE)} - LATER_COLUMNS
        if have != want:
            raise RuntimeError(f"{TABLE} already exists but differs from what 0008_question_images creates (missing "
                               f"{sorted(want - have)}, unexpected {sorted(have - want)}). Fix the table by hand before upgrading.")
    else:
        op.create_table(
            TABLE, *_columns(),
            sa.UniqueConstraint("provider_id", "checksum", name="uq_question_image_assets_provider_checksum"),
            sa.CheckConstraint("storage IN ('db')", name="question_image_assets_storage_check"),
            sa.CheckConstraint("file_size > 0", name="question_image_assets_size_check"),
        )
        op.create_index("ix_question_image_assets_provider_id", TABLE, ["provider_id"])

    have = {c["name"]: c for c in inspector.get_columns("question_versions")}
    for name in IMAGE_COLUMNS:
        if name in have:
            if not have[name]["nullable"]:
                raise RuntimeError(f"question_versions.{name} already exists but is NOT NULL. Fix the column by hand before upgrading.")
            continue
        with op.batch_alter_table("question_versions") as batch:      # batch: SQLite cannot add a foreign key with ALTER TABLE
            batch.add_column(sa.Column(name, sa.Uuid(), nullable=True))
            # the name PostgreSQL gives the same key when the table is created from the models (0001 baseline)
            batch.create_foreign_key(f"question_versions_{name}_fkey", TABLE, [name], ["asset_id"])


def downgrade() -> None:
    # only for an unused database: dropping the columns loses which question showed which picture
    with op.batch_alter_table("question_versions") as batch:
        for name in IMAGE_COLUMNS:
            batch.drop_constraint(f"question_versions_{name}_fkey", type_="foreignkey")
            batch.drop_column(name)
    op.drop_table(TABLE)
