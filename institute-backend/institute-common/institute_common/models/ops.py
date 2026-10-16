from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from institute_common.database import Base, JSONType, UTCDateTime, utcnow


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','IN_PROGRESS','RESOLVED','CLOSED')", name="support_tickets_status_check"),
        CheckConstraint("priority IN ('LOW','NORMAL','HIGH')", name="support_tickets_priority_check"),
    )

    ticket_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"), index=True)
    branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.branch_id"))
    created_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN", server_default="OPEN")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="NORMAL", server_default="NORMAL")
    # Cross-domain escalation links to the ORIGINAL ticket (e.g. student-portal) instead of copying it.
    linked_ticket_ref: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class SupportTicketMessage(Base):
    __tablename__ = "support_ticket_messages"

    message_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("support_tickets.ticket_id"), nullable=False, index=True)
    author_staff_id: Mapped[UUID] = mapped_column(nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    __table_args__ = (CheckConstraint("price >= 0", name="subscription_plans_price_check"),)

    plan_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    billing_period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    max_students: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class ProviderSubscription(Base):
    __tablename__ = "provider_subscriptions"

    subscription_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("subscription_plans.plan_id"), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("status IN ('ISSUED','PAID','PARTIALLY_PAID','VOID')", name="invoices_status_check"),
        CheckConstraint("amount > 0", name="invoices_amount_check"),
    )

    invoice_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    subscription_id: Mapped[UUID | None] = mapped_column(ForeignKey("provider_subscriptions.subscription_id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ISSUED", server_default="ISSUED")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class PaymentRecord(Base):
    """A payment confirmed by staff against external proof (no payment gateway is integrated).
    The idempotency key makes a retried request record the payment once."""

    __tablename__ = "payment_records"
    __table_args__ = (CheckConstraint("amount > 0", name="payment_records_amount_check"),)

    payment_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.invoice_id"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    recorded_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class BillingAdjustment(Base):
    """Refunds and exceptional adjustments: Super Admin only, reason mandatory, append-only."""

    __tablename__ = "billing_adjustments"
    __table_args__ = (CheckConstraint("kind IN ('REFUND','CREDIT','WRITE_OFF')", name="billing_adjustments_kind_check"),)

    adjustment_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.invoice_id"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor_staff_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class InstituteSetting(Base):
    __tablename__ = "institute_settings"
    __table_args__ = (
        CheckConstraint("scope IN ('PLATFORM','PROVIDER','BRANCH')", name="institute_settings_scope_check"),
        UniqueConstraint("scope", "scope_id", "key", name="uq_setting"),
    )

    setting_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column()
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSONType)
    updated_by: Mapped[UUID] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))
