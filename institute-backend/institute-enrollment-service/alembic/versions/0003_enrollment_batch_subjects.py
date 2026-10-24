"""subject-wise enrollment: the subjects a student takes in a batch membership

Additive only: creates the new table `enrollment_batch_subjects`; no existing table, column or row is changed. A batch membership
without rows keeps its original meaning (every subject the batch offers), so existing enrollments, scheduled exams, attempts and
results behave exactly as before.

Revision ID: 0003_enrollment_batch_subjects
Revises: 0002_enrollment_student_identity
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_enrollment_batch_subjects"
down_revision = "0002_enrollment_student_identity"
branch_labels = None
depends_on = None


def _public_search_path() -> None:
    # Transaction-local: a pooled connection may carry an empty session search_path (see alembic/env.py).
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL search_path TO public")


def upgrade() -> None:
    _public_search_path()
    if "enrollment_batch_subjects" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "enrollment_batch_subjects",
        sa.Column("enrollment_batch_subject_id", sa.Uuid(), primary_key=True),
        sa.Column("enrollment_batch_id", sa.Uuid(), sa.ForeignKey("enrollment_batches.enrollment_batch_id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("enrollment_batch_id", "subject_id", name="uq_enrollment_batch_subject"),
    )
    op.create_index("ix_enrollment_batch_subjects_enrollment_batch_id", "enrollment_batch_subjects", ["enrollment_batch_id"])


def downgrade() -> None:
    _public_search_path()
    op.drop_index("ix_enrollment_batch_subjects_enrollment_batch_id", table_name="enrollment_batch_subjects")
    op.drop_table("enrollment_batch_subjects")
