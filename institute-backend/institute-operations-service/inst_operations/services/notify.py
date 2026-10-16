"""Delivery of staff notifications (the operations service owns the inbox). Called through its internal API by the other services.
Idempotent staff notifications. The dedupe key makes retries (and replayed jobs) safe:
the same event never produces a second notification or email."""
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core.email import Mail, get_sender
from institute_common.models.exams import Notification
from institute_common.models.staff import Staff


def deliver(db: Session, staff_ids, *, kind: str, title: str, body: str, dedupe: str,
           provider_id: UUID | None = None, email: bool = True) -> int:
    sent = 0
    for sid in dict.fromkeys(staff_ids):
        key = f"{dedupe}:{sid}"
        n = Notification(recipient_staff_id=sid, provider_id=provider_id, kind=kind, title=title, body=body, dedupe_key=key)
        db.add(n)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            continue  # already delivered for this event
        sent += 1
        if email:
            staff = db.get(Staff, sid)
            if staff and staff.status == "ACTIVE" and staff.email_verified_at is not None:
                try:
                    get_sender().send(Mail(to=staff.email, subject=title, body=body))
                    n.email_status = "SENT"
                except Exception:
                    n.email_status = "FAILED"  # visible for reconciliation; never blocks the workflow
    db.commit()
    return sent
