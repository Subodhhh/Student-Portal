"""Delivery of a service's audit spool to the identity service (the audit log's single writer), and the ingestion side.

Forwarding is at-least-once and idempotent: the spool row id becomes the audit row id, so a replay inserts nothing. Several
forwarders may share one spool (two processes of a service, or two deployments on one database): each claims its rows with
FOR UPDATE SKIP LOCKED, so no event is sent by two forwarders at once."""
import logging
import threading
import time
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql, sqlite
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
    # the claim (row locks) lasts one delivery attempt: it ends with the commit below, on success or failure
    rows = db.scalars(select(spool).where(spool.forwarded_at.is_(None)).order_by(spool.created_at).limit(BATCH)
                      .with_for_update(skip_locked=True)).all()
    if not rows:
        db.commit()
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
    """Identity side: insert events (idempotent by event_id). `actor_service` is forced to the authenticated sender. One lookup and
    one insert per batch (not one round trip per event), and ON CONFLICT DO NOTHING: two senders delivering the same event at
    once insert it once, and neither fails."""
    from datetime import datetime
    rows: dict[UUID, dict] = {}
    for e in events:
        eid = UUID(str(e["event_id"]))
        rows.setdefault(eid, dict(
            audit_id=eid, occurred_at=datetime.fromisoformat(e["occurred_at"]), actor_staff_id=_uuid(e.get("actor_staff_id")),
            actor_service=f"institute-{service}", action=str(e["action"])[:80], target_type=e.get("target_type"), target_id=e.get("target_id"),
            provider_id=_uuid(e.get("provider_id")), branch_id=_uuid(e.get("branch_id")), outcome=e["outcome"], reason=e.get("reason"),
            ip_address=e.get("ip_address"), details=e.get("details")))
    known = set(db.scalars(select(AuditLog.audit_id).where(AuditLog.audit_id.in_(list(rows))))) if rows else set()
    new = [r for eid, r in rows.items() if eid not in known]
    inserted = 0
    if new:
        dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
        inserted = len(db.execute(dialect.insert(AuditLog).values(new).on_conflict_do_nothing(index_elements=["audit_id"])
                                  .returning(AuditLog.audit_id)).all())
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
