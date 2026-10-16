"""Per-service transactional audit spool. Services other than identity never write the audit log directly: they append an event to
their own spool table in the same database transaction as the action, and a forwarder delivers it to the identity service
(idempotent by event_id). Nothing is lost if identity is briefly unavailable."""
from uuid import uuid4

from sqlalchemy import Index, Integer, String, Uuid
from sqlalchemy.orm import mapped_column

from institute_common.database import Base, JSONType, UTCDateTime, utcnow

SPOOL_SERVICES = ("organization", "academics", "enrollment", "paper", "exam", "operations")
SPOOLS: dict[str, type] = {}


def _make(service: str) -> type:
    name = f"inst_{service}_audit_spool"
    return type(
        f"AuditSpool_{service}", (Base,),
        {
            "__tablename__": name,
            "__table_args__": (Index(f"ix_{name}_pending", "forwarded_at", "created_at"),),
                        "event_id": mapped_column(Uuid, primary_key=True, default=uuid4),
            "payload": mapped_column(JSONType, nullable=False),
            "created_at": mapped_column(UTCDateTime, nullable=False, default=utcnow),
            "forwarded_at": mapped_column(UTCDateTime),
            "attempts": mapped_column(Integer, nullable=False, default=0),
            "last_error": mapped_column(String(300)),
        },
    )


for _svc in SPOOL_SERVICES:
    SPOOLS[_svc] = _make(_svc)
