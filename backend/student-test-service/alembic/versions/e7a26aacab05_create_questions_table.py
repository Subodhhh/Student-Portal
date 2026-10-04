"""create questions table

Revision ID: e7a26aacab05
Revises: b36306966afb
Create Date: 2026-10-04 05:28:07.132024
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a26aacab05"
down_revision: Union[str, Sequence[str], None] = "b36306966afb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column(
            "question_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "test_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "question_order",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "question_text",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "question_type",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "option_a",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "option_b",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "option_c",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "option_d",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "correct_answer",
            sa.String(length=10),
            nullable=False,
        ),
        sa.Column(
            "marks",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "negative_marks",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "image_reference",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.test_id"],
            name="fk_questions_test_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("question_id"),
        sa.UniqueConstraint(
            "test_id",
            "question_order",
            name="uq_questions_test_order",
        ),
    )

    op.create_index(
        "ix_questions_test_id",
        "questions",
        ["test_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_questions_test_id",
        table_name="questions",
    )

    op.drop_table("questions")