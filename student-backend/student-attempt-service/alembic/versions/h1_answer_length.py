"""Answers of MSQ and NAT questions: attempt_answers.selected_answer VARCHAR(10) -> VARCHAR(64)

An MSQ answer is up to 8 letters ("ACDH"), a NAT answer a number of up to 12 significant digits ("-123456.123456"); answers are
validated and canonicalised before they are stored (app/question_types.py). Widening a VARCHAR only changes the catalogue in
PostgreSQL (no table rewrite). Downgrade refuses while any answer is longer than 10 characters.

Revision ID: h1_answer_length
Revises: attempt_lookup_index
"""
import sqlalchemy as sa

from alembic import op

revision = "h1_answer_length"
down_revision = "attempt_lookup_index"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("attempt_answers", "selected_answer", type_=sa.String(64), existing_nullable=True)


def downgrade():
    longer = op.get_bind().execute(sa.text("SELECT count(*) FROM attempt_answers WHERE length(selected_answer) > 10")).scalar()
    if longer:
        raise RuntimeError(f"{longer} answer(s) are longer than 10 characters; downgrade refused.")
    op.alter_column("attempt_answers", "selected_answer", type_=sa.String(10), existing_nullable=True)
