"""Read-only internal API for the Institute backend: attempt status per exam (no answers, no marks).

Lets the Institute side mark each assignment STARTED / COMPLETED / MISSED from what students actually did.
Authenticated by X-Service-Key; 404 unless INSTITUTE_SERVICE_API_KEYS is configured."""
import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.test_attempt import TestAttempt

MAX_ROWS = 20000


def require_service_key(x_service_key: str | None = Header(default=None)) -> None:
    keys = [k.strip() for k in settings.institute_service_api_keys.split(",") if k.strip()]
    if not keys:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_service_key or not any(hmac.compare_digest(x_service_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_service_key)])


@router.get("/tests/{test_id}/attempts")
def test_attempts(test_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = db.scalars(select(TestAttempt).where(TestAttempt.test_id == test_id).limit(MAX_ROWS)).all()
    return [
        {"student_id": r.student_id, "attempt_id": str(r.attempt_id), "status": r.status.value,
         "started_at": r.started_at.isoformat(), "expires_at": r.expires_at.isoformat(),
         "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None}
        for r in rows
    ]
