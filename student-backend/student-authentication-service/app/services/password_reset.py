import hashlib
import secrets
from datetime import datetime, timedelta, timezone


TOKEN_DURATION = timedelta(minutes=30)


def generate_reset_token() -> tuple[str, str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + TOKEN_DURATION

    return raw_token, token_hash, expires_at

def issue_reset_link(db, student) -> datetime:
    """Invalidate the student's unused reset tokens, store a new one (hash only) and send the link. Shared by the public
    forgot-password endpoint and the staff-initiated setup link. Returns the new token's expiry (UTC)."""
    from app.config import settings
    from app.models.password_reset_token import PasswordSetupResetToken
    from app.services.email import email_enabled, send_password_reset_email

    now = datetime.now(timezone.utc)
    old_tokens = (
        db.query(PasswordSetupResetToken)
        .filter(
            PasswordSetupResetToken.student_id == student.student_id,
            PasswordSetupResetToken.token_type == "PASSWORD_RESET",
            PasswordSetupResetToken.used_at.is_(None),
            PasswordSetupResetToken.expires_at > now.replace(tzinfo=None),
        )
        .all()
    )
    for token in old_tokens:
        token.used_at = now.replace(tzinfo=None)
    raw_token, token_hash, expires_at = generate_reset_token()
    db.add(PasswordSetupResetToken(student_id=student.student_id, token_hash=token_hash, token_type="PASSWORD_RESET",
                                   expires_at=expires_at.replace(tzinfo=None)))
    db.commit()
    if email_enabled():
        send_password_reset_email(recipient_email=student.email, reset_url=f"{settings.frontend_reset_url}?token={raw_token}")
    return expires_at
