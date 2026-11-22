"""Staff notifications raised by the paper service (bulk upload: preview ready, import finished). The inbox belongs to the operations
service, so this service never writes it: a notification is stored in paper_notification_outbox (in the same transaction as the event,
idempotent by key) and the bulk-import worker delivers it through operations' internal API, retrying what could not be delivered -
the exam service's pattern (inst_exam/services/notify.py)."""
import hashlib
import random
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.database import utcnow
from institute_common.internal_http import InternalServiceError, peer
from institute_common.models.bulk_import import PaperNotificationOutbox

MAX_ATTEMPTS = 8


def queue(db: Session, staff_ids, *, kind: str, title: str, body: str, dedupe: str, provider_id: UUID | None = None) -> bool:
    """Add one notification to the outbox (in the caller's transaction); False when the same one was already queued."""
    ids = sorted({str(s) for s in staff_ids if s})
    if not ids:
        return False
    key = f"{dedupe}:{hashlib.sha1(','.join(ids).encode()).hexdigest()[:12]}"[:200]
    if db.scalar(select(PaperNotificationOutbox.outbox_id).where(PaperNotificationOutbox.dedupe_key == key)):
        return False
    db.add(PaperNotificationOutbox(dedupe_key=key, payload={
        "staff_ids": ids, "kind": kind, "title": title[:255], "body": body[:5000], "dedupe": dedupe[:180],
        "provider_id": str(provider_id) if provider_id else None, "email": False}))    # in-app (the bell); no email
    return True


def deliver_pending(db: Session, now=None, client=None) -> dict:
    """Send due notifications to operations; a refusal (4xx) is final, an outage is retried with backoff."""
    now = now or utcnow()
    stats = {"sent": 0, "retry": 0, "failed": 0}
    rows = db.scalars(select(PaperNotificationOutbox).where(PaperNotificationOutbox.status == "PENDING",
                                                            PaperNotificationOutbox.next_attempt_at <= now)
                      .order_by(PaperNotificationOutbox.created_at).limit(50)).all()
    for row in rows:
        row.attempts += 1
        try:
            resp = (client or peer("operations")).request("POST", "/notifications", json=row.payload)
            if resp.status_code == 200:
                row.status, row.sent_at, row.last_error = "SENT", now, None
                stats["sent"] += 1
                continue
            row.last_error = f"operations answered {resp.status_code}"[:300]
            permanent = 400 <= resp.status_code < 500
        except InternalServiceError as exc:
            row.last_error, permanent = str(exc)[:300], False
        if permanent or row.attempts >= MAX_ATTEMPTS:
            row.status = "FAILED"
            stats["failed"] += 1
        else:
            row.next_attempt_at = now + timedelta(seconds=min(900, 15 * 2 ** row.attempts) * (0.5 + random.random()))
            stats["retry"] += 1
    db.commit()
    return stats
