"""Append-only audit trail. Never pass secrets, tokens, OTPs or session ids in details."""
import ipaddress
import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.staff import AuditLog

log = logging.getLogger("institute.audit")

def _valid_ip(value: str | None) -> str | None:
    """INET columns reject non-IP text; audit must never fail because of an odd peer address."""
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


_FORBIDDEN_KEYS = {"password", "token", "otp", "session", "secret", "api_key", "authorization"}


def _clean(details: dict | None) -> dict | None:
    if not details:
        return None
    return {
        k: ("[redacted]" if any(f in k.lower() for f in _FORBIDDEN_KEYS) else v)
        for k, v in details.items()
    }


def record(
    db: Session,
    action: str,
    outcome: str,
    *,
    actor: UUID | None = None,
    service: str | None = None,
    target_type: str | None = None,
    target_id=None,
    provider_id: UUID | None = None,
    branch_id: UUID | None = None,
    reason: str | None = None,
    ip: str | None = None,
    details: dict | None = None,
    commit: bool = True,
) -> None:
    db.add(
        AuditLog(
            actor_staff_id=actor,
            actor_service=service,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            provider_id=provider_id,
            branch_id=branch_id,
            outcome=outcome,
            reason=reason,
            ip_address=_valid_ip(ip),
            details=_clean(details),
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    if outcome != "SUCCESS":
        log.warning("audit action=%s outcome=%s target=%s/%s", action, outcome, target_type, target_id)
