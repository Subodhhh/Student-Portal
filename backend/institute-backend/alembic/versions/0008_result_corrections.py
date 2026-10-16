"""result corrections (immutable request/reason/actor; mutable execution state)

Revision ID: 0008_result_corrections
Revises: 0007_student_notifications
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_result_corrections"
down_revision = "0007_student_notifications"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")
JSONT = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "result_corrections",
        sa.Column("correction_id", sa.Uuid(), primary_key=True),
        sa.Column("exam_id", sa.Uuid(), sa.ForeignKey("exams.exam_id"), nullable=False),
        sa.Column("exam_version_id", sa.Uuid(), sa.ForeignKey("exam_versions.exam_version_id"), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("student_id", sa.String(20)),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request", JSONT, nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("actor_staff_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("outcome", JSONT),
        sa.Column("error", sa.String(500)),
        sa.Column("applied_at", TS),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint("kind IN ('MARKS_ADJUSTMENT','ANSWER_KEY')", name="result_corrections_kind_check"),
        sa.CheckConstraint("status IN ('PENDING','APPLIED','FAILED')", name="result_corrections_status_check"),
    )
    op.create_index("ix_result_corrections_exam", "result_corrections", ["exam_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        # Who asked for what, and why, can never be rewritten or deleted; only the execution state may change.
        op.execute("CREATE TRIGGER result_corrections_immutable BEFORE UPDATE OF correction_id, exam_id, exam_version_id, provider_id, "
                   "branch_id, kind, student_id, idempotency_key, request, reason, actor_staff_id, created_at OR DELETE "
                   "ON result_corrections FOR EACH ROW EXECUTE FUNCTION institute_history_immutable();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS result_corrections_immutable ON result_corrections")
    op.drop_table("result_corrections")
