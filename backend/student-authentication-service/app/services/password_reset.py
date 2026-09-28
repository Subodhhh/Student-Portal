import hashlib
import secrets
from datetime import datetime, timedelta, timezone


TOKEN_DURATION = timedelta(minutes=30)


def generate_reset_token() -> tuple[str, str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + TOKEN_DURATION

    return raw_token, token_hash, expires_at