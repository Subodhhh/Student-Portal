"""Expiry of composite-test invitations that were not answered by the request's deadline (worker step; idempotent).

Pending slots of an OPEN request whose `respond_by` has passed become EXPIRED and the request closes as EXPIRED. Papers already
contributed stay shared with the initiator (they can still be used in an exam plan); nothing else changes. The initiator's decision
makers and every provider whose invitation lapsed are told once (notification dedupe keys)."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.database import utcnow
from institute_common.models.exams import CompositeRequest, CompositeRequestSlot
from institute_common.models.org import Provider
from inst_exam.services.notify import notify


def expire_due(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    done = 0
    for r in db.scalars(select(CompositeRequest).where(CompositeRequest.status == "OPEN", CompositeRequest.respond_by.is_not(None),
                                                      CompositeRequest.respond_by <= now).with_for_update(skip_locked=True)):
        slots = list(db.scalars(select(CompositeRequestSlot).where(CompositeRequestSlot.request_id == r.request_id)))
        lapsed = [s for s in slots if s.status == "PENDING"]
        for s in lapsed:
            s.status, s.responded_at = "EXPIRED", now
        r.status, r.closed_at = "EXPIRED", now
        db.commit()
        done += 1
        audit.record(db, "composite.expire", "SUCCESS", actor=None, target_type="composite_request", target_id=r.request_id,
                     provider_id=r.initiator_provider_id, details={"expired_slots": len(lapsed),
                                                                    "contributed": sum(1 for s in slots if s.status == "CONTRIBUTED")})
        from inst_exam.routes.composite import _deciders_of, _initiator_staff   # same recipients as the request's other events
        contributed = sum(1 for s in slots if s.status == "CONTRIBUTED")
        notify(db, _initiator_staff(db, r), kind="COMPOSITE_EXPIRED", title=f"Composite request expired: {r.name}",
               body=f"{len(lapsed)} subject(s) were not contributed before the deadline. {contributed} contributed paper(s) remain "
                    "available for your exam plans.", dedupe=f"composite:{r.request_id}:expired", provider_id=r.initiator_provider_id)
        initiator = db.get(Provider, r.initiator_provider_id)
        for provider_id in dict.fromkeys(s.invited_provider_id for s in lapsed if s.invited_provider_id != r.initiator_provider_id):
            notify(db, _deciders_of(db, provider_id), kind="COMPOSITE_EXPIRED", title=f"Invitation expired: {r.name}",
                   body=f"The request from {initiator.name if initiator else 'another provider'} closed before you responded.",
                   dedupe=f"composite:{r.request_id}:{provider_id}:expired", provider_id=provider_id)
    return done
