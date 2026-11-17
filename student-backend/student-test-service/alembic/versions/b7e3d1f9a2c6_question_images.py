"""Pictures for questions and options (additive)

* New table question_images: each picture once, named by its SHA-256 checksum (the Institute sends it with the exam).
* questions gains five nullable checksum references: question_image and option_a_image .. option_d_image.

Existing rows are not touched: the new columns are NULL, and adding a nullable column without a default does not rewrite the table.

Revision ID: b7e3d1f9a2c6
Revises: a3f1c7d9e2b4
"""
import sqlalchemy as sa
from alembic import op

revision = "b7e3d1f9a2c6"
down_revision = "a3f1c7d9e2b4"
branch_labels = None
depends_on = None

COLUMNS = ("question_image", "option_a_image", "option_b_image", "option_c_image", "option_d_image")


def upgrade() -> None:
    op.create_table(
        "question_images",
        sa.Column("checksum", sa.String(64), primary_key=True),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("image_data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in COLUMNS:
        op.add_column("questions", sa.Column(name, sa.String(64), nullable=True))
        op.create_foreign_key(f"questions_{name}_fkey", "questions", "question_images", [name], ["checksum"])


def downgrade() -> None:
    for name in COLUMNS:
        op.drop_constraint(f"questions_{name}_fkey", "questions", type_="foreignkey")
        op.drop_column("questions", name)
    op.drop_table("question_images")
