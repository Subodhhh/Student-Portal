"""Delivery of a service's audit spool to the identity service (the audit log's single writer), and the ingestion side.

Forwarding is at-least-once and idempotent: the spool row id becomes the audit row id, so a replay inserts nothing."""
import logging
import threading
import time
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.context import service_context
from institute_common.database import new_session, utcnow
from institute_common.internal_http import InternalServiceError, peer
from institute_common.models.audit_spool import SPOOLS
from institute_common.models.staff import AuditLog

log = logging.getLogger("institute.audit.forward")
BATCH = 100
KEEP_FORWARDED = timedelta(days=7)


def forward_spool(db: Session, service: str, client=None) -> dict:
    """Send pending events to identity. Returns counts; never raises (the spool keeps what could not be delivered)."""
    spool = SPOOLS[service]
    rows = db.scalars(select(spool).where(spool.forwarded_at.is_(None)).order_by(spool.created_at).limit(BATCH)).all()
    if not rows:
        return {"sent": 0, "pending": 0}
    body = {"service": service, "events": [{"event_id": str(r.event_id), **r.payload} for r in rows]}
    try:
        http = client or peer("identity")
        resp = http.request("POST", "/audit/events", json=body)
        if resp.status_code != 200:
            raise InternalServiceError(f"identity answered {resp.status_code}")
    except Exception as exc:  # noqa: BLE001 - keep the spool, retry later
        for r in rows:
            r.attempts += 1
            r.last_error = str(exc)[:300]
        db.commit()
        log.warning("audit forward failed for %s: %s", service, type(exc).__name__)
        return {"sent": 0, "pending": len(rows)}
    now = utcnow()
    for r in rows:
        r.forwarded_at = now
    db.execute(delete(spool).where(spool.forwarded_at < now - KEEP_FORWARDED))
    db.commit()
    return {"sent": len(rows), "pending": 0}


def ingest_events(db: Session, service: str, events: list[dict]) -> int:
    """Identity side: insert events (idempotent by event_id). `actor_service` is forced to the authenticated sender."""
    from datetime import datetime
    inserted = 0
    for e in events:
        eid = UUID(str(e["event_id"]))
        if db.get(AuditLog, eid):
            continue
        db.add(AuditLog(
            audit_id=eid, occurred_at=datetime.fromisoformat(e["occurred_at"]), actor_staff_id=_uuid(e.get("actor_staff_id")),
            actor_service=f"institute-{service}", action=str(e["action"])[:80], target_type=e.get("target_type"), target_id=e.get("target_id"),
            provider_id=_uuid(e.get("provider_id")), branch_id=_uuid(e.get("branch_id")), outcome=e["outcome"], reason=e.get("reason"),
            ip_address=e.get("ip_address"), details=e.get("details")))
        inserted += 1
    db.commit()
    return inserted


def _uuid(v):
    return UUID(str(v)) if v else None


def start_forwarder(service: str) -> None:
    """Background loop inside a service process (disabled when AUDIT_FORWARD_INTERVAL_SECONDS=0)."""
    interval = settings.audit_forward_interval_seconds
    if interval <= 0:
        return

    def loop():
        while True:
            time.sleep(interval)
            try:
                with service_context(service):
                    db = new_session()
                    try:
                        forward_spool(db, service)
                    finally:
                        db.close()
            except Exception:  # noqa: BLE001
                log.exception("audit forwarder iteration failed")

    threading.Thread(target=loop, name=f"audit-forward-{service}", daemon=True).start()
