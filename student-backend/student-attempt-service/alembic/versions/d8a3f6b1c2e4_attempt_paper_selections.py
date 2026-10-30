"""Global Test paper selection (additive)

  * paper_selections: a student's current choice of paper for a SELECTABLE subject of a test (one per test, student and subject);
    changeable only while the student has no attempt for that test.
  * attempt_selected_papers: the attempt's paper per subject, written in the same transaction as the attempt and never changed;
    an authorized retake copies its original's rows. Attempts without rows (every existing attempt, Daily/Weekly, composite and Global
    Tests made before paper choice) keep receiving all of the test's questions.
paper_id is the test service's questions.paper_id: no foreign key across services.

Revision ID: attempt_paper_selections
Revises: attempt_integrity_reviews
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "attempt_paper_selections"
down_revision = "attempt_integrity_reviews"
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade():
    op.create_table(
        "paper_selections",
        sa.Column("test_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(20), primary_key=True),
        sa.Column("subject", sa.String(100), primary_key=True),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )

    op.create_table(
        "attempt_selected_papers",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_attempts.attempt_id"), primary_key=True),
        sa.Column("subject", sa.String(100), primary_key=True),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )


def downgrade():
    op.drop_table("attempt_selected_papers")
    op.drop_table("paper_selections")
