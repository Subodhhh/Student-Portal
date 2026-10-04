"""create attempt answers table

Revision ID: fe7584138b98
Revises: 4928c47d3639
Create Date: 2026-10-04 10:50:34.422117

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fe7584138b98"
down_revision: Union[str, Sequence[str], None] = "4928c47d3639"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attempt_answers",
        sa.Column("answer_id", sa.UUID(), nullable=False),
        sa.Column("attempt_id", sa.UUID(), nullable=False),
        sa.Column("question_id", sa.UUID(), nullable=False),
        sa.Column("selected_answer", sa.String(length=10), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["test_attempts.attempt_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("answer_id"),
        sa.UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_attempt_answers_attempt_question",
        ),
    )


def downgrade() -> None:
    op.drop_table("attempt_answers")