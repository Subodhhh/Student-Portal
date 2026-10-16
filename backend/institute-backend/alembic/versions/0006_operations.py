"""support, billing, settings (additive)

Revision ID: 0006_operations
Revises: 0005_exams
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_operations"
down_revision = "0005_exams"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")
JSONT = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("ticket_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id")),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id")),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("priority", sa.String(10), nullable=False, server_default="NORMAL"),
        sa.Column("linked_ticket_ref", sa.String(120)),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("status IN ('OPEN','IN_PROGRESS','RESOLVED','CLOSED')", name="support_tickets_status_check"),
        sa.CheckConstraint("priority IN ('LOW','NORMAL','HIGH')", name="support_tickets_priority_check"),
    )
    op.create_index("ix_support_tickets_provider_id", "support_tickets", ["provider_id"])
    op.create_index("ix_support_tickets_created_by", "support_tickets", ["created_by"])
    op.create_table(
        "support_ticket_messages",
        sa.Column("message_id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("support_tickets.ticket_id"), nullable=False),
        sa.Column("author_staff_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
    )
    op.create_index("ix_support_ticket_messages_ticket_id", "support_ticket_messages", ["ticket_id"])
    op.create_table(
        "subscription_plans",
        sa.Column("plan_id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("billing_period_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_students", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("code"),
        sa.CheckConstraint("price >= 0", name="subscription_plans_price_check"),
    )
    op.create_table(
        "provider_subscriptions",
        sa.Column("subscription_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("subscription_plans.plan_id"), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
    )
    op.create_index("ix_provider_subscriptions_provider_id", "provider_subscriptions", ["provider_id"])
    op.create_table(
        "invoices",
        sa.Column("invoice_id", sa.Uuid(), primary_key=True),
        sa.Column("invoice_number", sa.String(30), nullable=False),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), sa.ForeignKey("provider_subscriptions.subscription_id")),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ISSUED"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("invoice_number"),
        sa.CheckConstraint("status IN ('ISSUED','PAID','PARTIALLY_PAID','VOID')", name="invoices_status_check"),
        sa.CheckConstraint("amount > 0", name="invoices_amount_check"),
    )
    op.create_index("ix_invoices_provider_id", "invoices", ["provider_id"])
    op.create_table(
        "payment_records",
        sa.Column("payment_id", sa.Uuid(), primary_key=True),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.invoice_id"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("method", sa.String(30), nullable=False),
        sa.Column("external_reference", sa.String(120), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint("amount > 0", name="payment_records_amount_check"),
    )
    op.create_index("ix_payment_records_invoice_id", "payment_records", ["invoice_id"])
    op.create_table(
        "billing_adjustments",
        sa.Column("adjustment_id", sa.Uuid(), primary_key=True),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.invoice_id"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("actor_staff_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint("kind IN ('REFUND','CREDIT','WRITE_OFF')", name="billing_adjustments_kind_check"),
    )
    op.create_index("ix_billing_adjustments_invoice_id", "billing_adjustments", ["invoice_id"])
    op.create_table(
        "institute_settings",
        sa.Column("setting_id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("scope_id", sa.Uuid()),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("value", JSONT),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("scope", "scope_id", "key", name="uq_setting"),
        sa.CheckConstraint("scope IN ('PLATFORM','PROVIDER','BRANCH')", name="institute_settings_scope_check"),
    )
    if op.get_bind().dialect.name == "postgresql":
        # Financial history is append-only for the application role.
        for t in ("payment_records", "billing_adjustments"):
            op.execute(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE OR DELETE ON {t} "
                       "FOR EACH ROW EXECUTE FUNCTION institute_history_immutable();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in ("payment_records", "billing_adjustments"):
            op.execute(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
    for t in ("institute_settings", "billing_adjustments", "payment_records", "invoices", "provider_subscriptions",
              "subscription_plans", "support_ticket_messages", "support_tickets"):
        op.drop_table(t)
