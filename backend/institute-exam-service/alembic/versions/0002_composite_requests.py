"""composite requests: cross-provider contribution workflow (new tables only; nothing existing is altered)

Revision ID: 0002_exam_composite_requests
Revises: 0001_exam_baseline
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_exam_composite_requests"
down_revision = "0001_exam_baseline"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "composite_requests",
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("note", sa.String(1000)),
        sa.Column("initiator_provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("initiator_branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("closed_at", TS),
        sa.Column("cancel_reason", sa.String(1000)),
        sa.CheckConstraint("status IN ('OPEN','COMPLETE','CANCELLED')", name="composite_requests_status_check"),
        sa.UniqueConstraint("created_by", "idempotency_key", name="uq_composite_idempotency"),
    )
    op.create_index("ix_composite_requests_initiator_provider_id", "composite_requests", ["initiator_provider_id"])
    op.create_index("uq_composite_open_name", "composite_requests", ["initiator_branch_id", "name"], unique=True,
                    postgresql_where=sa.text("status = 'OPEN'"), sqlite_where=sa.text("status = 'OPEN'"))
    op.create_table(
        "composite_request_slots",
        sa.Column("slot_id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("composite_requests.request_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("invited_provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("paper_id", sa.Uuid()),
        sa.Column("paper_version_id", sa.Uuid()),
        sa.Column("responded_by", sa.Uuid()),
        sa.Column("responded_at", TS),
        sa.Column("decline_reason", sa.String(1000)),
        sa.CheckConstraint("status IN ('PENDING','CONTRIBUTED','DECLINED')", name="composite_request_slots_status_check"),
        sa.UniqueConstraint("request_id", "subject_id", name="uq_composite_slot_subject"),
    )
    op.create_index("ix_composite_request_slots_request_id", "composite_request_slots", ["request_id"])
    op.create_index("ix_composite_request_slots_invited_provider_id", "composite_request_slots", ["invited_provider_id"])


def downgrade() -> None:
    # Forward-only by policy; the tables hold audit-relevant workflow history.
    pass
