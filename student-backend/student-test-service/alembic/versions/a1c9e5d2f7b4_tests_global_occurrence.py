"""tests.global_occurrence_id: unified Global Test exams (additive, nullable)

Existing tests stay NULL and keep showing their provider and branch.

Revision ID: a1c9e5d2f7b4
Revises: 7c2df5f674d5
"""

from alembic import op
import sqlalchemy as sa


revision = "a1c9e5d2f7b4"
down_revision = "7c2df5f674d5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tests", sa.Column("global_occurrence_id", sa.Uuid(), nullable=True))


def downgrade():
    op.drop_column("tests", "global_occurrence_id")
