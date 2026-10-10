"""add subject to questions

Revision ID: add_question_subject
Revises: e7a26aacab05
"""

from alembic import op
import sqlalchemy as sa


revision = "add_question_subject"
down_revision = "e7a26aacab05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "questions",
        sa.Column(
            "subject",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_questions_test_subject",
        "questions",
        ["test_id", "subject"],
    )


def downgrade():
    op.drop_index(
        "ix_questions_test_subject",
        table_name="questions",
    )

    op.drop_column(
        "questions",
        "subject",
    )
