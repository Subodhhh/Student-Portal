"""Audit log view (the identity service owns the log; every other service delivers its events here through the audit spool)."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.core.roles import Role
from institute_common.core.security import Principal, get_principal
from institute_common.database import get_db
from institute_common.models.staff import AuditLog
from institute_common.util import paginate

router = APIRouter(prefix="/api/v1", tags=["Operations"])


@router.get("/audit-logs")
def audit_logs(action: str | None = Query(default=None, max_length=80), outcome: str | None = Query(default=None, pattern="^(SUCCESS|DENIED|FAILURE)$"),
               actor: UUID | None = None, since: datetime | None = None, page: int = 1, page_size: int = 50,
               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
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
    if since:
        stmt = stmt.where(AuditLog.occurred_at >= since)
    res = paginate(db, stmt.order_by(AuditLog.occurred_at.desc()), page, page_size)
    res["items"] = [{"audit_id": str(a.audit_id), "at": a.occurred_at.isoformat(), "actor": str(a.actor_staff_id) if a.actor_staff_id else a.actor_service,
                     "action": a.action, "target_type": a.target_type, "target_id": a.target_id, "provider_id": str(a.provider_id) if a.provider_id else None,
                     "outcome": a.outcome, "reason": a.reason} for a in res["items"]]
    return res


