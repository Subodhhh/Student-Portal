"""student notification outbox; assignment sync/notification markers (additive)

Revision ID: 0007_student_notifications
Revises: 0006_operations
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_student_notifications"
down_revision = "0006_operations"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")
JSONT = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.add_column("student_exam_assignments", sa.Column("status_updated_at", TS))
    op.add_column("student_exam_assignments", sa.Column("result_notified_at", TS))
    op.create_table(
        "student_notification_outbox",
        sa.Column("outbox_id", sa.Uuid(), primary_key=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", JSONT),
        sa.Column("student_ids", JSONT, nullable=False),
        sa.Column("provider_id", sa.Uuid()),
        sa.Column("branch_id", sa.Uuid()),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("sent_at", TS),
        sa.UniqueConstraint("dedupe_key"),
        sa.CheckConstraint("status IN ('PENDING','SENT','FAILED')", name="student_notification_outbox_status_check"),
    )
    op.create_index("ix_student_notification_outbox_due", "student_notification_outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("student_notification_outbox")
    op.drop_column("student_exam_assignments", "result_notified_at")
    op.drop_column("student_exam_assignments", "status_updated_at")
