"""Internal API used by the Institute backend to put entries into students' existing inbox.

Creates ordinary rows in `notifications` / `notification_recipients`, so the student-facing read, unread-count and mark-read
endpoints work unchanged. Idempotent per `dedupe_key` (kept in the notification metadata, so no schema change is needed).
Authenticated by X-Service-Key; the whole router answers 404 unless INSTITUTE_SERVICE_API_KEYS is configured."""
import hmac
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.notification import Notification, NotificationEventType, NotificationRecipient, RecipientType


def require_service_key(x_service_key: str | None = Header(default=None)) -> None:
    keys = [k.strip() for k in settings.institute_service_api_keys.split(",") if k.strip()]
    if not keys:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_service_key or not any(hmac.compare_digest(x_service_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_service_key)])

STUDENT_ID = r"^[A-Za-z0-9_-]{1,50}$"


class InternalNotification(BaseModel):
    dedupe_key: str = Field(min_length=8, max_length=200)
    event_type: NotificationEventType
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=5000)
    metadata: dict | None = None
    student_ids: list[str] = Field(min_length=1, max_length=5000)


@router.post("/notifications", status_code=201)
def create_notification(body: InternalNotification, db: Session = Depends(get_db)):
    if any(not re.match(STUDENT_ID, s) for s in body.student_ids):
        raise HTTPException(status_code=422, detail="Invalid student id")
    existing = db.scalar(select(Notification).where(Notification.event_metadata["dedupe_key"].as_string() == body.dedupe_key))
    if existing:
        # Replay: acknowledged, never adds recipients or a second entry.
        count = len(existing.recipients)
        return JSONResponse(status_code=200, content={"notification_id": str(existing.notification_id), "created": False, "recipients": count})
    n = Notification(event_type=body.event_type, title=body.title, message=body.message,
                     event_metadata={**(body.metadata or {}), "dedupe_key": body.dedupe_key, "source": "institute"})
    for sid in dict.fromkeys(body.student_ids):
        n.recipients.append(NotificationRecipient(recipient_type=RecipientType.STUDENT, recipient_id=sid))
    db.add(n)
    db.commit()
    return {"notification_id": str(n.notification_id), "created": True, "recipients": len(n.recipients)}
