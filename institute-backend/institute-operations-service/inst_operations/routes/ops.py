"""Notification inbox, support tickets, billing, settings, audit view and role dashboards."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.roles import GLOBAL_ROLES, Role
from institute_common.core.security import Principal, api_error, can_see_provider, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import EnrollmentTransfer, Subject, TeacherSubjectAssignment
from institute_common.models.exams import (
    Exam, ExamGenerationJob, ExamNotificationOutbox, ExamOutbox, ExamVersion, Notification, ResultCorrection, StudentNotificationOutbox,
)
from institute_common.models.ops import (
    BillingAdjustment, InstituteSetting, Invoice, PaymentRecord, ProviderSubscription, SubscriptionPlan,
    SupportTicket, SupportTicketMessage,
)
from institute_common.models.org import Batch, Branch, Enrollment, Provider
from institute_common.models.papers import Paper, PaperVersion
from institute_common.models.staff import AuditLog, Staff, StaffRoleAssignment
from institute_common.util import need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from institute_common.codes import next_code

router = APIRouter(prefix="/api/v1", tags=["Operations"])


# ------------------------------------------------------------------ scope helper

def scope_clause(p: Principal, perm: str, provider_col, branch_col):
    """SQL predicate limiting rows to what `p` may see for `perm` (default deny => false())."""
    clauses = []
    for a in p.assignments:
        role = Role(a.role)
        from institute_common.core.roles import ROLE_PERMISSIONS
        if perm not in ROLE_PERMISSIONS[role]:
            continue
        if role in GLOBAL_ROLES:
            return true()
        if role == Role.PROVIDER_ADMIN:
            clauses.append(provider_col == a.provider_id)
        elif role == Role.BRANCH_ADMIN:
            clauses.append(branch_col == a.branch_id)
        elif role == Role.TEACHER:
            clauses += [branch_col == t.branch_id for t in p.teaching]
    return or_(*clauses) if clauses else ~true()


# ------------------------------------------------------------------ notifications inbox

class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    notification_id: UUID
    kind: str
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None


@router.get("/notifications", response_model=Page[NotificationOut])
def my_notifications(unread_only: bool = False, page: int = 1, page_size: int = 25,
                     p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.recipient_staff_id == p.staff_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return paginate(db, stmt.order_by(Notification.created_at.desc()), page, page_size)


@router.get("/notifications/unread-count")
def unread_count(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    n = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.recipient_staff_id == p.staff_id, Notification.read_at.is_(None)))
    return {"unread_count": n or 0}


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or n.recipient_staff_id != p.staff_id:     # only the recipient; others get a plain 404
        raise api_error(404, "NOT_FOUND", "Notification not found.")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return n


@router.post("/notifications/read-all")
def mark_all_read(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.recipient_staff_id == p.staff_id, Notification.read_at.is_(None))).all()
    for n in rows:
        n.read_at = utcnow()
    db.commit()
    return {"marked": len(rows)}


# ------------------------------------------------------------------ support

class TicketIn(Strict):
    subject: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    provider_id: UUID | None = None
    branch_id: UUID | None = None
    priority: str = Field(default="NORMAL", pattern="^(LOW|NORMAL|HIGH)$")
    linked_ticket_ref: str | None = Field(default=None, max_length=120)


class MessageIn(Strict):
    body: str = Field(min_length=1, max_length=5000)


class TicketStatusIn(Strict):
    status: str = Field(pattern="^(OPEN|IN_PROGRESS|RESOLVED|CLOSED)$")


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    ticket_id: UUID
    provider_id: UUID | None
    branch_id: UUID | None
    created_by: UUID
    subject: str
    status: str
    priority: str
    linked_ticket_ref: str | None
    created_at: datetime


def _can_manage_ticket(p: Principal, t: SupportTicket) -> bool:
    if p.is_global:
        return True
    if t.provider_id is None:
        return False
    return p.can("support.manage", provider_id=t.provider_id, branch_id=t.branch_id) if t.branch_id else (
        p.can("support.manage", provider_id=t.provider_id))


def _ticket(db: Session, p: Principal, ticket_id: UUID) -> SupportTicket:
    t = db.get(SupportTicket, ticket_id)
    if not t or not (t.created_by == p.staff_id or _can_manage_ticket(p, t)):
        if t:
            audit.record(db, "authz.out_of_scope", "DENIED", actor=p.staff_id, target_type="ticket", target_id=ticket_id,
                         provider_id=t.provider_id, ip=p.ip)
        raise api_error(404, "NOT_FOUND", "Ticket not found.")      # no existence leak across tenants
    return t


@router.post("/support/tickets", response_model=TicketOut, status_code=201)
def create_ticket(body: TicketIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if body.provider_id and not can_see_provider(p, body.provider_id):
        raise api_error(403, "FORBIDDEN", "You do not belong to that provider.")
    if body.branch_id:
        br = db.get(Branch, body.branch_id)
        if not br or br.provider_id != body.provider_id:
            raise api_error(422, "INVALID_SCOPE", "Branch does not belong to that provider.")
    t = SupportTicket(provider_id=body.provider_id, branch_id=body.branch_id, created_by=p.staff_id, subject=body.subject,
                      priority=body.priority, linked_ticket_ref=body.linked_ticket_ref)
    db.add(t)
    db.flush()
    db.add(SupportTicketMessage(ticket_id=t.ticket_id, author_staff_id=p.staff_id, body=body.body))
    db.commit()
    audit.record(db, "support.create", "SUCCESS", actor=p.staff_id, target_type="ticket", target_id=t.ticket_id,
                 provider_id=t.provider_id, ip=p.ip)
    return t


@router.get("/support/tickets", response_model=Page[TicketOut])
def list_tickets(status: str | None = Query(default=None, pattern="^(OPEN|IN_PROGRESS|RESOLVED|CLOSED)$"), page: int = 1, page_size: int = 25,
                 p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    stmt = select(SupportTicket).where(or_(SupportTicket.created_by == p.staff_id,
                                           scope_clause(p, "support.manage", SupportTicket.provider_id, SupportTicket.branch_id)))
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    return paginate(db, stmt.order_by(SupportTicket.created_at.desc()), page, page_size)


@router.get("/support/tickets/{ticket_id}")
def get_ticket(ticket_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    t = _ticket(db, p, ticket_id)
    msgs = db.scalars(select(SupportTicketMessage).where(SupportTicketMessage.ticket_id == ticket_id).order_by(SupportTicketMessage.created_at)).all()
    return {"ticket": TicketOut.model_validate(t),
            "messages": [{"author": str(m.author_staff_id), "body": m.body, "at": m.created_at.isoformat()} for m in msgs]}


@router.post("/support/tickets/{ticket_id}/messages", status_code=201)
def add_message(ticket_id: UUID, body: MessageIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    t = _ticket(db, p, ticket_id)
    if t.status == "CLOSED":
        raise api_error(409, "TICKET_CLOSED", "This ticket is closed.")
    db.add(SupportTicketMessage(ticket_id=ticket_id, author_staff_id=p.staff_id, body=body.body))
    t.updated_at = utcnow()
    db.commit()
    return {"message": "Added."}


@router.patch("/support/tickets/{ticket_id}", response_model=TicketOut)
def set_ticket_status(ticket_id: UUID, body: TicketStatusIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    t = _ticket(db, p, ticket_id)
    if not _can_manage_ticket(p, t) and not (t.created_by == p.staff_id and body.status == "CLOSED"):
        raise api_error(403, "FORBIDDEN", "You may only close your own tickets.")
    t.status = body.status
    db.commit()
    audit.record(db, "support.status", "SUCCESS", actor=p.staff_id, target_type="ticket", target_id=ticket_id,
                 provider_id=t.provider_id, ip=p.ip, details={"status": body.status})
    return t


# ------------------------------------------------------------------ billing

Money = Field(gt=0, lt=Decimal("100000000"), max_digits=12, decimal_places=2)


class PlanIn(Strict):
    code: str = Field(pattern=r"^[A-Z0-9_]{2,40}$")
    name: str = Field(min_length=2, max_length=120)
    price: Decimal = Field(ge=0, lt=Decimal("100000000"), max_digits=12, decimal_places=2)
    billing_period_days: int = Field(default=30, gt=0, le=366)
    max_students: int | None = Field(default=None, gt=0)


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    plan_id: UUID
    code: str
    name: str
    price: Decimal
    billing_period_days: int
    max_students: int | None
    status: str


class SubscriptionIn(Strict):
    plan_id: UUID
    starts_on: date
    ends_on: date | None = None


class InvoiceIn(Strict):
    amount: Decimal = Money
    due_on: date
    subscription_id: UUID | None = None


class PaymentIn(Strict):
    amount: Decimal = Money
    method: str = Field(pattern="^(BANK_TRANSFER|CHEQUE|CASH|UPI|CARD|OTHER)$")
    external_reference: str = Field(min_length=3, max_length=120)


class AdjustmentIn(Strict):
    kind: str = Field(pattern="^(REFUND|CREDIT|WRITE_OFF)$")
    amount: Decimal = Money
    reason: str


def _invoice_view(db: Session, inv: Invoice) -> dict:
    paid = db.scalar(select(func.coalesce(func.sum(PaymentRecord.amount), 0)).where(PaymentRecord.invoice_id == inv.invoice_id)) or Decimal(0)
    adj = {k: (a or Decimal(0)) for k, a in db.execute(select(BillingAdjustment.kind, func.sum(BillingAdjustment.amount))
                                                      .where(BillingAdjustment.invoice_id == inv.invoice_id).group_by(BillingAdjustment.kind))}
    net_paid = Decimal(paid) - Decimal(adj.get("REFUND", 0))
    balance = Decimal(inv.amount) - net_paid - Decimal(adj.get("CREDIT", 0)) - Decimal(adj.get("WRITE_OFF", 0))
    return {"invoice_id": str(inv.invoice_id), "invoice_number": inv.invoice_number, "provider_id": str(inv.provider_id),
            "amount": f"{inv.amount:.2f}", "currency": inv.currency, "due_on": inv.due_on.isoformat(), "status": inv.status,
            "paid": f"{paid:.2f}", "refunded": f"{Decimal(adj.get('REFUND', 0)):.2f}", "net_paid": f"{net_paid:.2f}",
            "credited": f"{Decimal(adj.get('CREDIT', 0)):.2f}", "written_off": f"{Decimal(adj.get('WRITE_OFF', 0)):.2f}",
            "balance": f"{balance:.2f}"}


def _recompute_status(db: Session, inv: Invoice) -> None:
    v = _invoice_view(db, inv)
    if inv.status == "VOID":
        return
    inv.status = "PAID" if Decimal(v["balance"]) <= 0 else ("PARTIALLY_PAID" if Decimal(v["net_paid"]) > 0 else "ISSUED")


def _invoice(db: Session, p: Principal, invoice_id: UUID, perm: str) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv or not (p.is_global or p.can(perm, provider_id=inv.provider_id)):
        if inv:
            audit.record(db, "authz.out_of_scope", "DENIED", actor=p.staff_id, target_type="invoice", target_id=invoice_id,
                         provider_id=inv.provider_id, ip=p.ip)
        raise api_error(404, "NOT_FOUND", "Invoice not found.")
    return inv


def _require_idem(key: str | None) -> str:
    if not key or not (8 <= len(key) <= 100):
        raise api_error(422, "IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header (8-100 characters).")
    return key


@router.post("/billing/plans", response_model=PlanOut, status_code=201)
def create_plan(body: PlanIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.is_global and p.has_perm("billing.routine")):
        p.require(db, "billing.routine")
    plan = SubscriptionPlan(**body.model_dump())
    db.add(plan)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "PLAN_EXISTS", "A plan with this code already exists.")
    audit.record(db, "billing.plan_create", "SUCCESS", actor=p.staff_id, target_type="plan", target_id=plan.plan_id, ip=p.ip)
    return plan


@router.get("/billing/plans", response_model=list[PlanOut])
def list_plans(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.has_perm("billing.read"):
        p.require(db, "billing.read")
    return db.scalars(select(SubscriptionPlan).where(SubscriptionPlan.status == "ACTIVE").order_by(SubscriptionPlan.price)).all()


@router.post("/providers/{provider_id}/subscription", status_code=201)
def assign_subscription(provider_id: UUID, body: SubscriptionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.is_global:
        p.require(db, "billing.routine")
    if not db.get(Provider, provider_id) or not db.get(SubscriptionPlan, body.plan_id):
        raise api_error(404, "NOT_FOUND", "Provider or plan not found.")
    if body.ends_on and body.ends_on < body.starts_on:
        raise api_error(422, "INVALID_DATES", "ends_on must not precede starts_on.")
    for old in db.scalars(select(ProviderSubscription).where(ProviderSubscription.provider_id == provider_id, ProviderSubscription.status == "ACTIVE")):
        old.status = "ENDED"
    s = ProviderSubscription(provider_id=provider_id, plan_id=body.plan_id, starts_on=body.starts_on, ends_on=body.ends_on, created_by=p.staff_id)
    db.add(s)
    db.commit()
    audit.record(db, "billing.subscription_assign", "SUCCESS", actor=p.staff_id, target_type="subscription", target_id=s.subscription_id,
                 provider_id=provider_id, ip=p.ip)
    return {"subscription_id": str(s.subscription_id)}


@router.get("/providers/{provider_id}/subscription")
def get_subscription(provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "billing.read", provider_id=provider_id)
    s = db.scalar(select(ProviderSubscription).where(ProviderSubscription.provider_id == provider_id, ProviderSubscription.status == "ACTIVE"))
    if not s:
        return {"subscription": None}
    plan = db.get(SubscriptionPlan, s.plan_id)
    return {"subscription": {"plan": plan.name, "code": plan.code, "price": f"{plan.price:.2f}", "starts_on": s.starts_on.isoformat(),
                             "ends_on": s.ends_on.isoformat() if s.ends_on else None}}


@router.post("/providers/{provider_id}/invoices", status_code=201)
def create_invoice(provider_id: UUID, body: InvoiceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.is_global and p.has_perm("billing.routine")):
        p.require(db, "billing.routine")
    if not db.get(Provider, provider_id):
        raise api_error(404, "NOT_FOUND", "Provider not found.")
    for _ in range(5):
        inv = Invoice(invoice_number=next_code(db, Invoice.invoice_number, f"INV-{utcnow().year}-", 5), provider_id=provider_id,
                      subscription_id=body.subscription_id, amount=body.amount, due_on=body.due_on, created_by=p.staff_id)
        db.add(inv)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
    else:
        raise api_error(503, "NUMBER_ALLOCATION_FAILED", "Please retry.")
    audit.record(db, "billing.invoice_create", "SUCCESS", actor=p.staff_id, target_type="invoice", target_id=inv.invoice_id,
                 provider_id=provider_id, ip=p.ip, details={"amount": str(body.amount)})
    return _invoice_view(db, inv)


@router.get("/providers/{provider_id}/invoices")
def list_invoices(provider_id: UUID, page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "billing.read", provider_id=provider_id)
    res = paginate(db, select(Invoice).where(Invoice.provider_id == provider_id).order_by(Invoice.created_at.desc()), page, page_size)
    res["items"] = [_invoice_view(db, i) for i in res["items"]]
    return res


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    inv = _invoice(db, p, invoice_id, "billing.read")
    view = _invoice_view(db, inv)
    view["payments"] = [{"amount": f"{x.amount:.2f}", "method": x.method, "reference": x.external_reference, "at": x.created_at.isoformat()}
                        for x in db.scalars(select(PaymentRecord).where(PaymentRecord.invoice_id == invoice_id).order_by(PaymentRecord.created_at))]
    view["adjustments"] = [{"kind": x.kind, "amount": f"{x.amount:.2f}", "reason": x.reason, "at": x.created_at.isoformat()}
                           for x in db.scalars(select(BillingAdjustment).where(BillingAdjustment.invoice_id == invoice_id).order_by(BillingAdjustment.created_at))]
    return view


@router.post("/invoices/{invoice_id}/payments", status_code=201)
def record_payment(invoice_id: UUID, body: PaymentIn, idempotency_key: str | None = Header(default=None),
                   p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Records a payment confirmed against external proof. No gateway is integrated, so this never
    claims a transaction succeeded on its own."""
    key = _require_idem(idempotency_key)
    if not (p.is_global and p.has_perm("billing.routine")):
        p.require(db, "billing.routine")
    inv = _invoice(db, p, invoice_id, "billing.routine")
    prior = db.scalar(select(PaymentRecord).where(PaymentRecord.idempotency_key == key))
    if prior:
        if prior.invoice_id != invoice_id or prior.amount != body.amount:
            raise api_error(409, "IDEMPOTENCY_CONFLICT", "That key was used for a different payment.")
        return _invoice_view(db, inv)                           # replay: recorded once
    v = _invoice_view(db, inv)
    if inv.status == "VOID" or body.amount > Decimal(v["balance"]):
        raise api_error(409, "INVALID_PAYMENT", "Payment exceeds the outstanding balance or the invoice is void.")
    db.add(PaymentRecord(invoice_id=invoice_id, idempotency_key=key, amount=body.amount, method=body.method,
                         external_reference=body.external_reference, recorded_by=p.staff_id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _invoice_view(db, _invoice(db, p, invoice_id, "billing.routine"))
    _recompute_status(db, inv)
    db.commit()
    audit.record(db, "billing.payment_record", "SUCCESS", actor=p.staff_id, target_type="invoice", target_id=invoice_id,
                 provider_id=inv.provider_id, ip=p.ip, details={"amount": str(body.amount), "method": body.method})
    return _invoice_view(db, inv)


@router.post("/invoices/{invoice_id}/adjustments", status_code=201)
def create_adjustment(invoice_id: UUID, body: AdjustmentIn, idempotency_key: str | None = Header(default=None),
                      p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Refunds and exceptional adjustments: Super Admin only; reason, actor and amount are permanent."""
    key = _require_idem(idempotency_key)
    perm = "billing.refund" if body.kind == "REFUND" else "billing.adjust"
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise api_error(404, "NOT_FOUND", "Invoice not found.")
    p.require(db, perm, provider_id=inv.provider_id)
    reason = need_reason(body.reason)
    prior = db.scalar(select(BillingAdjustment).where(BillingAdjustment.idempotency_key == key))
    if prior:
        if prior.invoice_id != invoice_id or prior.amount != body.amount or prior.kind != body.kind:
            raise api_error(409, "IDEMPOTENCY_CONFLICT", "That key was used for a different adjustment.")
        return _invoice_view(db, inv)
    v = _invoice_view(db, inv)
    limit = Decimal(v["net_paid"]) if body.kind == "REFUND" else Decimal(v["balance"])
    if body.amount > limit:
        raise api_error(409, "INVALID_ADJUSTMENT", "Amount exceeds what can be " + ("refunded." if body.kind == "REFUND" else "adjusted."))
    db.add(BillingAdjustment(invoice_id=invoice_id, idempotency_key=key, kind=body.kind, amount=body.amount, reason=reason, actor_staff_id=p.staff_id))
    db.flush()
    _recompute_status(db, inv)
    db.commit()
    audit.record(db, f"billing.{body.kind.lower()}", "SUCCESS", actor=p.staff_id, target_type="invoice", target_id=invoice_id,
                 provider_id=inv.provider_id, reason=reason, ip=p.ip, details={"amount": str(body.amount)})
    return _invoice_view(db, inv)


# ------------------------------------------------------------------ settings

SETTING_KEYS: dict[str, tuple[type, Any]] = {
    "exam.default_duration_minutes": (int, lambda v: 1 <= v <= 720),
    "notifications.email_enabled": (bool, lambda v: True),
    "branding.display_name": (str, lambda v: 1 <= len(v) <= 100),
}


class SettingIn(Strict):
    key: str
    value: Any


def _check_setting(key: str, value: Any) -> None:
    spec = SETTING_KEYS.get(key)
    ok = spec and type(value) is spec[0] and spec[1](value)
    if not ok:
        raise api_error(422, "INVALID_SETTING", "Unknown setting key or invalid value.")


def _put_setting(db: Session, p: Principal, scope: str, scope_id: UUID | None, body: SettingIn, provider_id=None, branch_id=None):
    _check_setting(body.key, body.value)
    row = db.scalar(select(InstituteSetting).where(InstituteSetting.scope == scope, InstituteSetting.key == body.key,
                                                   InstituteSetting.scope_id == scope_id if scope_id else InstituteSetting.scope_id.is_(None)))
    old = row.value if row else None
    if row:
        row.value, row.updated_by = body.value, p.staff_id
    else:
        db.add(InstituteSetting(scope=scope, scope_id=scope_id, key=body.key, value=body.value, updated_by=p.staff_id))
    db.commit()
    audit.record(db, "settings.update", "SUCCESS", actor=p.staff_id, target_type=f"setting:{scope.lower()}", target_id=scope_id,
                 provider_id=provider_id, branch_id=branch_id, ip=p.ip, details={"key": body.key, "old": old, "new": body.value})
    return {body.key: body.value}


def _get_settings(db: Session, scope: str, scope_id: UUID | None) -> dict:
    cond = InstituteSetting.scope_id == scope_id if scope_id else InstituteSetting.scope_id.is_(None)
    return {r.key: r.value for r in db.scalars(select(InstituteSetting).where(InstituteSetting.scope == scope, cond))}


@router.get("/settings/platform")
def get_platform_settings(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.is_global:
        p.require(db, "settings.platform")
    return _get_settings(db, "PLATFORM", None)


@router.put("/settings/platform")
def put_platform_settings(body: SettingIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "settings.platform")                       # Super Admin only
    return _put_setting(db, p, "PLATFORM", None, body)


@router.get("/settings/providers/{provider_id}")
def get_provider_settings(provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "settings.provider", provider_id=provider_id)
    return _get_settings(db, "PROVIDER", provider_id)


@router.put("/settings/providers/{provider_id}")
def put_provider_settings(provider_id: UUID, body: SettingIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "settings.provider", provider_id=provider_id)
    return _put_setting(db, p, "PROVIDER", provider_id, body, provider_id=provider_id)


@router.get("/settings/branches/{branch_id}")
def get_branch_settings(branch_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = db.get(Branch, branch_id)
    if not br:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    p.require(db, "settings.branch", provider_id=br.provider_id, branch_id=branch_id)
    return _get_settings(db, "BRANCH", branch_id)


@router.put("/settings/branches/{branch_id}")
def put_branch_settings(branch_id: UUID, body: SettingIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = db.get(Branch, branch_id)
    if not br:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    p.require(db, "settings.branch", provider_id=br.provider_id, branch_id=branch_id)
    return _put_setting(db, p, "BRANCH", branch_id, body, provider_id=br.provider_id, branch_id=branch_id)


# ------------------------------------------------------------------ service health (platform operators)

@router.get("/system/health")
def system_health(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Readiness of every Institute service plus the failure queues that need an operator. Super Admin only (settings.platform)."""
    p.require(db, "settings.platform")
    from institute_common.config import SERVICES
    from institute_common.internal_http import readiness
    services = {name: ({"status": "ok", "database": "ok"} if name == "operations" else readiness(name)) for name in SERVICES}
    queues = {
        "failed_exam_deliveries": _count(db, select(ExamOutbox).where(ExamOutbox.status == "FAILED")),
        "failed_generation_jobs": _count(db, select(ExamGenerationJob).where(ExamGenerationJob.status == "FAILED")),
        "failed_student_notifications": _count(db, select(StudentNotificationOutbox).where(StudentNotificationOutbox.status == "FAILED")),
        "failed_staff_notifications": _count(db, select(ExamNotificationOutbox).where(ExamNotificationOutbox.status == "FAILED")),
        "unfinished_result_corrections": _count(db, select(ResultCorrection).where(ResultCorrection.status != "APPLIED")),
    }
    return {"checked_at": utcnow().isoformat(), "services": services, "queues": queues,
            "healthy": all(v.get("status") == "ok" for v in services.values()) and not any(queues.values())}


# ------------------------------------------------------------------ dashboards (real data only)

def _count(db: Session, stmt) -> int:
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def _paper_status_counts(db: Session, p: Principal) -> dict:
    stmt = (select(PaperVersion.status, func.count()).join(Paper, Paper.paper_id == PaperVersion.paper_id)
            .where(scope_clause(p, "paper.read", Paper.provider_id, Paper.branch_id)))
    if not p.can("paper.read", exclude_roles=(Role.TEACHER,), provider_id=None) and not p.is_global:
        taught = {t.subject_id for t in p.teaching}
        non_teacher = [a for a in p.assignments if Role(a.role) != Role.TEACHER]
        if not non_teacher:
            stmt = stmt.where(Paper.subject_id.in_(taught) if taught else false_())
    return dict(db.execute(stmt.group_by(PaperVersion.status)).all())


def false_():
    return ~true()


@router.get("/dashboard")
def dashboard(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    role = p.highest_role()
    now = utcnow()
    exam_scope = scope_clause(p, "exam.read", Exam.provider_id, Exam.branch_id)
    out: dict[str, Any] = {"role": role.value}
    upcoming = (select(Exam).join(ExamVersion, ExamVersion.exam_id == Exam.exam_id)
                .where(exam_scope, Exam.status != "CANCELLED", ExamVersion.start_at >= now))
    out["upcoming_exams"] = _count(db, upcoming)
    out["papers_by_status"] = _paper_status_counts(db, p)
    out["unread_notifications"] = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.recipient_staff_id == p.staff_id, Notification.read_at.is_(None))) or 0

    if role in GLOBAL_ROLES:
        out["providers"] = _count(db, select(Provider).where(Provider.status == "ACTIVE", Provider.deleted_at.is_(None)))
        out["branches"] = _count(db, select(Branch).where(Branch.status == "ACTIVE", Branch.deleted_at.is_(None)))
        out["staff"] = _count(db, select(Staff).where(Staff.status == "ACTIVE"))
        out["exams_by_status"] = dict(db.execute(select(Exam.status, func.count()).group_by(Exam.status)).all())
        out["open_tickets"] = _count(db, select(SupportTicket).where(SupportTicket.status.in_(("OPEN", "IN_PROGRESS"))))
        out["security"] = {
            "denied_events_24h": _count(db, select(AuditLog).where(AuditLog.outcome == "DENIED", AuditLog.occurred_at >= now - timedelta(hours=24))),
            "failed_logins_24h": _count(db, select(AuditLog).where(AuditLog.action == "auth.login", AuditLog.outcome == "FAILURE",
                                                                    AuditLog.occurred_at >= now - timedelta(hours=24))),
        }
        out["operations"] = {
            "failed_deliveries": _count(db, select(ExamOutbox).where(ExamOutbox.status == "FAILED")),
            "pending_deliveries": _count(db, select(ExamOutbox).where(ExamOutbox.status == "PENDING")),
            "failed_generation_jobs": _count(db, select(ExamGenerationJob).where(ExamGenerationJob.status == "FAILED")),
            "failed_student_notifications": _count(db, select(StudentNotificationOutbox).where(StudentNotificationOutbox.status == "FAILED")),
            "unfinished_corrections": _count(db, select(ResultCorrection).where(ResultCorrection.status != "APPLIED")),
        }
        out["unpaid_invoices"] = _count(db, select(Invoice).where(Invoice.status.in_(("ISSUED", "PARTIALLY_PAID"))))
    elif role == Role.PROVIDER_ADMIN:
        provs = {a.provider_id for a in p.assignments if Role(a.role) == Role.PROVIDER_ADMIN}
        out["branches"] = _count(db, select(Branch).where(Branch.provider_id.in_(provs), Branch.status == "ACTIVE", Branch.deleted_at.is_(None)))
        out["teachers"] = _count(db, select(StaffRoleAssignment).where(StaffRoleAssignment.provider_id.in_(provs), StaffRoleAssignment.role == "TEACHER",
                                                                       StaffRoleAssignment.status == "ACTIVE"))
        out["students"] = _count(db, select(Enrollment.student_id).where(Enrollment.provider_id.in_(provs), Enrollment.status == "ACTIVE").distinct())
        out["pending_transfers"] = _count(db, select(EnrollmentTransfer).where(EnrollmentTransfer.provider_id.in_(provs), EnrollmentTransfer.status == "PENDING"))
        out["open_tickets"] = _count(db, select(SupportTicket).where(SupportTicket.provider_id.in_(provs), SupportTicket.status.in_(("OPEN", "IN_PROGRESS"))))
        out["unpaid_invoices"] = _count(db, select(Invoice).where(Invoice.provider_id.in_(provs), Invoice.status.in_(("ISSUED", "PARTIALLY_PAID"))))
    elif role == Role.BRANCH_ADMIN:
        brs = {a.branch_id for a in p.assignments if Role(a.role) == Role.BRANCH_ADMIN}
        out["batches"] = _count(db, select(Batch).where(Batch.branch_id.in_(brs), Batch.status == "ACTIVE"))
        out["students"] = _count(db, select(Enrollment.student_id).where(Enrollment.branch_id.in_(brs), Enrollment.status == "ACTIVE").distinct())
        out["pending_transfers"] = _count(db, select(EnrollmentTransfer).where(EnrollmentTransfer.from_branch_id.in_(brs), EnrollmentTransfer.status == "PENDING"))
        out["teachers"] = _count(db, select(TeacherSubjectAssignment.staff_id).where(TeacherSubjectAssignment.branch_id.in_(brs),
                                                                                     TeacherSubjectAssignment.status == "ACTIVE").distinct())
    else:
        subjects = db.scalars(select(Subject).where(Subject.subject_id.in_({t.subject_id for t in p.teaching}))).all() if p.teaching else []
        out["teaching"] = [{"subject": s.name, "branch_id": str(t.branch_id), "batch_id": str(t.batch_id) if t.batch_id else None}
                           for t in p.teaching for s in subjects if s.subject_id == t.subject_id]
        out["my_papers"] = _count(db, select(Paper).where(Paper.created_by == p.staff_id))
    return out
