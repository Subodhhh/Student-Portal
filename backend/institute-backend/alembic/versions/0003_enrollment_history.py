"""enrollment events and transfers (additive)

Revision ID: 0003_enrollment_history
Revises: 0002_academics
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_enrollment_history"
down_revision = "0002_academics"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "enrollment_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("enrollment_id", sa.String(36), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("batch_id", sa.Uuid()),
        sa.Column("actor_staff_id", sa.Uuid()),
        sa.Column("reason", sa.String(1000)),
        sa.Column("occurred_at", TS, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_enrollment_events_enrollment_id", "enrollment_events", ["enrollment_id"])
    op.create_table(
        "enrollment_transfers",
        sa.Column("transfer_id", sa.Uuid(), primary_key=True),
        sa.Column("enrollment_id", sa.String(36), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.String(20), nullable=False),
        sa.Column("from_branch_id", sa.Uuid(), nullable=False),
        sa.Column("to_branch_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("reason", sa.String(1000)),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid()),
        sa.Column("decision_note", sa.String(1000)),
        sa.Column("new_enrollment_id", sa.String(36)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("decided_at", TS),
        sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="enrollment_transfers_status_check"),
    )
    op.create_index("ix_enrollment_transfers_enrollment_id", "enrollment_transfers", ["enrollment_id"])
    op.create_index("ix_enrollment_transfers_provider_id", "enrollment_transfers", ["provider_id"])


def downgrade() -> None:
    op.drop_table("enrollment_transfers")
    op.drop_table("enrollment_events")
