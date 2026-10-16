"""Internal API of the operations service: other Institute services deliver staff notifications through it."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from inst_operations.services.notify import deliver
from institute_common.database import get_db
from institute_common.internal_http import require_internal_key

router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_internal_key)])


class NotifyIn(BaseModel):
    staff_ids: list[UUID] = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=2, max_length=60)
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    dedupe: str = Field(min_length=3, max_length=180)
    provider_id: UUID | None = None
    email: bool = True


@router.post("/notifications")
def create_notifications(body: NotifyIn, db: Session = Depends(get_db)):
    """Idempotent per (dedupe, staff member): replaying a request never produces a second notification or email."""
    sent = deliver(db, body.staff_ids, kind=body.kind, title=body.title, body=body.body, dedupe=body.dedupe,
                   provider_id=body.provider_id, email=body.email)
    return {"requested": len(set(body.staff_ids)), "created": sent}
