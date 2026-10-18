"""Audit log view (the identity service owns the log; every other service delivers its events here through the audit spool)."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.core.roles import Role
from institute_common.core.security import Principal, get_principal
from institute_common.database import get_db
from institute_common.models.org import Provider
from institute_common.models.staff import AuditLog, Staff
from institute_common.util import paginate

router = APIRouter(prefix="/api/v1", tags=["Operations"])


@router.get("/audit-logs")
def audit_logs(action: str | None = Query(default=None, max_length=80), outcome: str | None = Query(default=None, pattern="^(SUCCESS|DENIED|FAILURE)$"),
               actor: UUID | None = None, actor_code: str | None = Query(default=None, max_length=20),
               since: datetime | None = None, until: datetime | None = None,
               provider_id: UUID | None = None, target_type: str | None = Query(default=None, max_length=60),
               target_id: str | None = Query(default=None, max_length=64), page: int = 1, page_size: int = 50,
               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Read-only audit history. Platform roles see everything; provider admins only their providers' entries."""
    if not p.has_perm("audit.read"):
        p.require(db, "audit.read")
    stmt = select(AuditLog)
    if not p.is_global:
        provs = {a.provider_id for a in p.assignments if Role(a.role) == Role.PROVIDER_ADMIN}
        stmt = stmt.where(AuditLog.provider_id.in_(provs)) if provs else stmt.where(False)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if outcome:
        stmt = stmt.where(AuditLog.outcome == outcome)
    if actor:
        stmt = stmt.where(AuditLog.actor_staff_id == actor)
    if actor_code:
        who = db.scalar(select(Staff.staff_id).where(Staff.staff_code == actor_code.strip().upper()))
        stmt = stmt.where(AuditLog.actor_staff_id == who) if who else stmt.where(False)
    if since:
        stmt = stmt.where(AuditLog.occurred_at >= since)
    if until:
        stmt = stmt.where(AuditLog.occurred_at < until)
    if provider_id:
        stmt = stmt.where(AuditLog.provider_id == provider_id)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    res = paginate(db, stmt.order_by(AuditLog.occurred_at.desc()), page, page_size)
    actors = {a.actor_staff_id for a in res["items"] if a.actor_staff_id}
    people = {sid: (code, name) for sid, code, name in db.execute(select(Staff.staff_id, Staff.staff_code, Staff.full_name).where(Staff.staff_id.in_(actors)))} if actors else {}
    provs = {a.provider_id for a in res["items"] if a.provider_id}
    prov_names = dict(db.execute(select(Provider.provider_id, Provider.name).where(Provider.provider_id.in_(provs))).all()) if provs else {}
    res["items"] = [{"audit_id": str(a.audit_id), "at": a.occurred_at.isoformat(), "actor": str(a.actor_staff_id) if a.actor_staff_id else a.actor_service,
                     "actor_code": people.get(a.actor_staff_id, (None, None))[0], "actor_name": people.get(a.actor_staff_id, (None, None))[1],
                     "action": a.action, "target_type": a.target_type, "target_id": a.target_id, "provider_id": str(a.provider_id) if a.provider_id else None,
                     "provider_name": prov_names.get(a.provider_id), "outcome": a.outcome, "reason": a.reason} for a in res["items"]]
    return res
