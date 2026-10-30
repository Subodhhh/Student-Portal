"""Global Test with paper choice: paper-tagged questions and per-student paper eligibility (additive)

  * questions.paper_id: the contribution (Institute submission id) of a question of a Global Test with paper choice; NULL otherwise.
  * student_paper_eligibility: each such student's frozen eligibility per subject (eligible papers, or why none), written once on delivery.
  * an index on tests.global_occurrence_id (the occurrence ranking scans by it).
Nothing existing is changed: Daily/Weekly tests, composite tests and existing Global Tests keep today's behaviour.

Revision ID: d5b8e2c4a7f1
Revises: c3d8f1a6b2e9
"""

import sqlalchemy as sa
from alembic import op

revision = "d5b8e2c4a7f1"
down_revision = "c3d8f1a6b2e9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("questions", sa.Column("paper_id", sa.Uuid(), nullable=True))
    op.create_index("ix_questions_test_paper", "questions", ["test_id", "paper_id"])
    op.create_index("ix_tests_global_occurrence_id", "tests", ["global_occurrence_id"])

    op.create_table(
        "student_paper_eligibility",
        sa.Column("eligibility_id", sa.Uuid(), primary_key=True),
        sa.Column("test_id", sa.Uuid(), sa.ForeignKey("tests.test_id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("paper_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('PAPER','SELECTABLE','NOT_ENROLLED','NO_SUBMISSION','NOT_APPROVED','NOT_COVERED','CONFLICT')",
                           name="ck_student_paper_eligibility_status"),
        sa.CheckConstraint("(status IN ('PAPER','SELECTABLE')) = (paper_id IS NOT NULL)", name="ck_student_paper_eligibility_paper"),
        sa.UniqueConstraint("test_id", "student_id", "subject", "paper_id", name="uq_student_paper_eligibility_paper"),
    )
    op.create_index("uq_student_paper_eligibility_unavailable", "student_paper_eligibility", ["test_id", "student_id", "subject"],
                    unique=True, postgresql_where=sa.text("paper_id IS NULL"))


def downgrade():
    op.drop_index("uq_student_paper_eligibility_unavailable", table_name="student_paper_eligibility")
    op.drop_table("student_paper_eligibility")
    op.drop_index("ix_tests_global_occurrence_id", table_name="tests")
    op.drop_index("ix_questions_test_paper", table_name="questions")
    op.drop_column("questions", "paper_id")
