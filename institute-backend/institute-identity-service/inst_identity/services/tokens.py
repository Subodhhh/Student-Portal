import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core.email import Mail, get_sender
from institute_common.database import utcnow
from institute_common.models.staff import Staff, StaffToken

EMAIL_VERIFY_TTL = timedelta(minutes=30)
PASSWORD_RESET_TTL = timedelta(minutes=15)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(db: Session, staff: Staff, purpose: str) -> str:
    """Create a single-use token (invalidating earlier unused ones of the same purpose)
    and email the link. Only the hash is stored."""
    ttl = EMAIL_VERIFY_TTL if purpose == "EMAIL_VERIFY" else PASSWORD_RESET_TTL
    db.execute(
        update(StaffToken)
        .where(
            StaffToken.staff_id == staff.staff_id,
            StaffToken.purpose == purpose,
            StaffToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )
    raw = secrets.token_urlsafe(32)
    db.add(StaffToken(staff_id=staff.staff_id, purpose=purpose, token_hash=_hash(raw), expires_at=utcnow() + ttl))
    db.commit()

    path = "verify-email" if purpose == "EMAIL_VERIFY" else "reset-password"
    link = f"{settings.frontend_base_url.rstrip('/')}/{path}?token={raw}"
    minutes = int(ttl.total_seconds() // 60)
    if purpose == "EMAIL_VERIFY":
        subject, lead = "Verify your Institute Portal email", "Verify your email and set your password"
    else:
        subject, lead = "Reset your Institute Portal password", "Reset your password"
    get_sender().send(
        Mail(
            to=staff.email,
            subject=subject,
            body=f"{lead}: {link}\n\nThis link expires in {minutes} minutes and works once. "
            "If you did not expect this email, ignore it.",
        )
    )
    return raw


def consume_token(db: Session, raw: str, purpose: str) -> Staff | None:
    """Validate and mark a token used. Returns the staff member or None."""
    tok = db.scalar(select(StaffToken).where(StaffToken.token_hash == _hash(raw), StaffToken.purpose == purpose))
    if not tok or tok.used_at is not None or tok.expires_at <= utcnow():
        return None
    # Single-use even under concurrent requests: conditional UPDATE.
    result = db.execute(
        update(StaffToken)
        .where(StaffToken.token_id == tok.token_id, StaffToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    staff = db.get(Staff, tok.staff_id)
    return staff if staff and staff.status == "ACTIVE" else None


def peek_token(db: Session, raw: str, purpose: str):
    """Look up a token without consuming it (to validate input first). Returns Staff or None."""
    tok = db.scalar(select(StaffToken).where(StaffToken.token_hash == _hash(raw), StaffToken.purpose == purpose))
    if not tok or tok.used_at is not None or tok.expires_at <= utcnow():
        return None
    return db.get(Staff, tok.staff_id)
