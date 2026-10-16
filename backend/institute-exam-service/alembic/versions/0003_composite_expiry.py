"""composite requests: optional response deadline and the EXPIRED state (additive; existing rows keep their status)

Revision ID: 0003_exam_composite_expiry
Revises: 0002_exam_composite_requests
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_exam_composite_expiry"
down_revision = "0002_exam_composite_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("composite_requests", sa.Column("respond_by", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("composite_requests") as b:
        b.drop_constraint("composite_requests_status_check", type_="check")
        b.create_check_constraint("composite_requests_status_check", "status IN ('OPEN','COMPLETE','CANCELLED','EXPIRED')")
    with op.batch_alter_table("composite_request_slots") as b:
        b.drop_constraint("composite_request_slots_status_check", type_="check")
        b.create_check_constraint("composite_request_slots_status_check", "status IN ('PENDING','CONTRIBUTED','DECLINED','EXPIRED')")
    op.create_index("ix_composite_requests_open_deadline", "composite_requests", ["respond_by"],
                    postgresql_where=sa.text("status = 'OPEN' AND respond_by IS NOT NULL"))


def downgrade() -> None:
    # Forward-only by policy (workflow history).
    pass
