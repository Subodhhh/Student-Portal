"""Student inbox notifications for Institute events (exam scheduled/cancelled, result available/updated).

Rows are written to a local outbox (idempotent by dedupe_key) inside the same transaction as the event that
caused them, then delivered to the Student notification service by the worker with bounded retries. A student
therefore gets each notification at most once, and nothing is lost if that service is down."""
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models.exams import StudentNotificationOutbox
from app.services.internal_http import InternalRejected, InternalServiceError
from app.services.notification_client import NotificationClient, get_notification_client
from app.services.notify import notify, responsible_staff

MAX_ATTEMPTS = 8
IST = ZoneInfo("Asia/Kolkata")  # display only; stored/compared times stay timezone-aware UTC


def fmt_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def enqueue(db: Session, *, dedupe_key: str, event_type: str, title: str, message: str, details: dict | None,
            student_ids: list[str], provider_id=None, branch_id=None) -> bool:
    """Queue one notification for several students. False if this dedupe_key was already queued.
    Flushes only; the caller's transaction commits it together with the event itself."""
    if not student_ids:
        return False
    exists = db.scalar(select(StudentNotificationOutbox.outbox_id).where(StudentNotificationOutbox.dedupe_key == dedupe_key))
    if exists:
        return False
    try:
        with db.begin_nested():
            db.add(StudentNotificationOutbox(dedupe_key=dedupe_key, event_type=event_type, title=title[:255], message=message,
                                             details=details, student_ids=sorted(set(student_ids)), provider_id=provider_id, branch_id=branch_id))
    except IntegrityError:  # lost a race to a concurrent enqueue of the same event
        return False
    return True


def deliver_due(db: Session, client: NotificationClient | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    stats = {"sent": 0, "retry": 0, "failed": 0}
    rows = db.scalars(select(StudentNotificationOutbox).where(StudentNotificationOutbox.status == "PENDING",
                                                              StudentNotificationOutbox.next_attempt_at <= now)
                      .order_by(StudentNotificationOutbox.created_at).limit(50)).all()
    for row in rows:
        row.attempts += 1
        try:
            (client or get_notification_client()).send(dedupe_key=row.dedupe_key, event_type=row.event_type, title=row.title,
                                                       message=row.message, metadata=row.details, student_ids=list(row.student_ids))
            row.status, row.sent_at, row.last_error = "SENT", now, None
            stats["sent"] += 1
        except (InternalServiceError, InternalRejected) as exc:
            row.last_error = str(exc)[:500]
            if isinstance(exc, InternalRejected) or row.attempts >= MAX_ATTEMPTS:
                row.status = "FAILED"
                stats["failed"] += 1
                if row.provider_id and row.branch_id:
                    notify(db, responsible_staff(db, row.provider_id, row.branch_id), kind="STUDENT_NOTIFICATION_FAILED",
                           title="A student notification could not be delivered", body=f"{row.title}: {row.last_error}",
                           dedupe=f"stunotifyfail:{row.outbox_id}", provider_id=row.provider_id)
            else:
                row.next_attempt_at = now + timedelta(seconds=min(3600, 30 * 2 ** row.attempts) * (0.5 + random.random()))
                stats["retry"] += 1
        db.commit()
    return stats


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
