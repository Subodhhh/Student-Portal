"""tests.shuffle_questions: a Daily/Weekly Test with Shuffle Questions (additive, NOT NULL default false)

True: each student gets their own fixed order of the test's questions (services/question_order.py; the attempt and result services
read it too, so run this migration before deploying them). Every existing test stays false, i.e. today's order.

Revision ID: f2a7c4e9b1d3
Revises: d5b8e2c4a7f1
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a7c4e9b1d3"
down_revision = "d5b8e2c4a7f1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tests", sa.Column("shuffle_questions", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade():
    op.drop_column("tests", "shuffle_questions")
