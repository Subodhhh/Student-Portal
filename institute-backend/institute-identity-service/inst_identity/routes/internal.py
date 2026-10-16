"""Internal API of the identity service (service-key protected; absent unless INTERNAL_API_KEYS is set)."""
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from institute_common.audit_forward import ingest_events
from institute_common.core.security import authenticate_session, load_assignments, load_teaching, principal_payload
from institute_common.database import get_db
from institute_common.internal_http import require_internal_key
from fastapi import HTTPException

router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_internal_key)])


class Introspect(BaseModel):
    session_id: str = Field(min_length=10, max_length=200)
    method: str = Field(max_length=10)
    csrf_token: str = Field(default="", max_length=200)


@router.post("/introspect")
def introspect(body: Introspect, db: Session = Depends(get_db)):
    """Who owns this browser session? Validates it (and slides the idle timeout), checks the CSRF token for unsafe methods and returns the
    caller's identity, role assignments and teaching assignments. 401/403 mirror what the public API answers."""
    staff, data = authenticate_session(db, body.session_id, body.method, body.csrf_token)
    return principal_payload(staff, load_assignments(db, staff.staff_id), load_teaching(db, staff.staff_id), data["csrf"])


class AuditBatch(BaseModel):
    service: str = Field(pattern="^(organization|academics|enrollment|paper|exam|operations)$")
    events: list[dict] = Field(max_length=500)


@router.post("/audit/events")
def audit_events(body: AuditBatch, x_service_name: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Idempotent ingestion of audit events spooled by another service."""
    try:
        inserted = ingest_events(db, body.service, body.events)
    except (KeyError, ValueError):
        db.rollback()
        raise HTTPException(status_code=422, detail="Malformed audit event")
    return {"received": len(body.events), "inserted": inserted}
