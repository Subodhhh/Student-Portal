"""Optional Class / Academic Year defaults per batch (prefill only)

Additive only: creates `batch_academic_defaults`; `batches` (whose DDL belongs to the Student enrollment chain) is not altered.

Revision ID: 0002_batch_academic_defaults
Revises: 0001_academics_baseline
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_batch_academic_defaults"
down_revision = "0001_academics_baseline"
branch_labels = None
depends_on = None

CLASSES = "'6TH', '7TH', '8TH', '9TH', '10TH', '11TH', '12TH', 'DROPPER', 'OTHER'"
YEAR = ("default_academic_year IS NULL OR (length(default_academic_year) = 9 AND substr(default_academic_year, 5, 1) = '-' "
        "AND CAST(substr(default_academic_year, 6, 4) AS INTEGER) = CAST(substr(default_academic_year, 1, 4) AS INTEGER) + 1)")


def _public_search_path() -> None:
    # Transaction-local: a pooled connection may carry an empty session search_path (see alembic/env.py).
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL search_path TO public")


def upgrade() -> None:
    _public_search_path()
    if "batch_academic_defaults" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "batch_academic_defaults",
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("batches.batch_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("default_class_level", sa.String(12)),
        sa.Column("default_academic_year", sa.String(9)),
        sa.Column("default_other_description", sa.String(120)),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(f"default_class_level IS NULL OR default_class_level IN ({CLASSES})", name="bad_class_level_check"),
        sa.CheckConstraint(YEAR, name="bad_academic_year_check"),
        sa.CheckConstraint("(default_class_level = 'OTHER' AND default_other_description IS NOT NULL) "
                           "OR ((default_class_level IS NULL OR default_class_level <> 'OTHER') AND default_other_description IS NULL)",
                           name="bad_other_description_check"),
    )


def downgrade() -> None:
    _public_search_path()
    op.drop_table("batch_academic_defaults")
