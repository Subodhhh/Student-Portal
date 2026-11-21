"""Marks awarded per question (decision D9): test_result_questions

One row per question of a result: its type, the stored answer, CORRECT / INCORRECT / UNANSWERED and the marks awarded (exact,
NUMERIC(8,2)). Written with the result; replaced when an authorized re-evaluation changes the result. Results evaluated earlier
have no rows (their review is recomputed with the same scorer). Downgrade refuses while rows exist.

Revision ID: i1_question_results
Revises: f5c0b7d2e9a4
"""
import sqlalchemy as sa

from alembic import op

revision = "i1_question_results"
down_revision = "f5c0b7d2e9a4"
branch_labels = None
depends_on = None


def upgrade():
    if "test_result_questions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table("test_result_questions",
                    sa.Column("result_id", sa.Uuid(), sa.ForeignKey("test_results.result_id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("question_id", sa.Uuid(), primary_key=True),
                    sa.Column("question_order", sa.Integer(), nullable=False),
                    sa.Column("question_type", sa.String(20), nullable=False),
                    sa.Column("selected_answer", sa.String(64), nullable=True),
                    sa.Column("status", sa.String(12), nullable=False),
                    sa.Column("marks_obtained", sa.Numeric(8, 2), nullable=False),
                    sa.CheckConstraint("status IN ('CORRECT','INCORRECT','UNANSWERED')", name="test_result_questions_status_check"))


def downgrade():
    rows = op.get_bind().execute(sa.text("SELECT count(*) FROM test_result_questions")).scalar()
    if rows:
        raise RuntimeError(f"{rows} per-question result row(s) exist; downgrade refused.")
    op.drop_table("test_result_questions")
