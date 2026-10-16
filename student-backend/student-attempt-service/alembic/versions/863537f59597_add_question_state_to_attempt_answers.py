"""add question state to attempt answers

Revision ID: add_question_state
Revises: add_unique_student_test_attempt
"""

from alembic import op
import sqlalchemy as sa


revision = "add_question_state"
down_revision = "add_unique_student_test_attempt"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "attempt_answers",
        sa.Column(
            "question_state",
            sa.String(length=30),
            nullable=True,
        ),
    )

    op.add_column(
        "attempt_answers",
        sa.Column(
            "visited_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.alter_column(
        "attempt_answers",
        "answered_at",
        existing_type=sa.DateTime(),
        nullable=True,
    )

    op.execute(
        """
        UPDATE attempt_answers
        SET question_state =
            CASE
                WHEN selected_answer IS NOT NULL
                    THEN 'ANSWERED'
                ELSE 'VISITED_UNANSWERED'
            END
        """
    )

    op.alter_column(
        "attempt_answers",
        "question_state",
        nullable=False,
    )


def downgrade():
    op.alter_column(
        "attempt_answers",
        "answered_at",
        existing_type=sa.DateTime(),
        nullable=False,
    )

    op.drop_column(
        "attempt_answers",
        "visited_at",
    )

    op.drop_column(
        "attempt_answers",
        "question_state",
    )