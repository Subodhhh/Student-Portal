"""Redis server-side sessions: one active session per staff account, 30 min
inactivity timeout, 12 h absolute lifetime. Keys live under ``inst_`` so they can
never be confused with the student portal's ``student_session:`` keys.

Cookie holds an opaque id only. The CSRF token is bound to the session."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from app.core.redis_store import client

IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_LIFETIME = timedelta(hours=12)
MFA_TTL = timedelta(minutes=5)
MFA_MAX_ATTEMPTS = 5
MFA_RESEND_COOLDOWN = timedelta(seconds=60)


def _skey(sid: str) -> str:
    return f"inst_session:{sid}"


def _active_key(staff_id: str) -> str:
    return f"inst_active:{staff_id}"


def create_session(staff_id: str) -> tuple[str, str]:
    """Create a fresh session (new random id => rotation) and revoke any previous one."""
    r = client()
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    r.set(
        _skey(sid),
        json.dumps(
            {
                "staff_id": staff_id,
                "csrf": csrf,
                "expires_at": (now + ABSOLUTE_LIFETIME).isoformat(),
            }
        ),
        ex=int(IDLE_TIMEOUT.total_seconds()),
    )
    previous = r.getset(_active_key(staff_id), sid)  # atomic swap: last login wins
    r.expire(_active_key(staff_id), int(ABSOLUTE_LIFETIME.total_seconds()))
    if previous and previous != sid:
        r.delete(_skey(previous))
    return sid, csrf


def get_session(sid: str) -> dict | None:
    r = client()
    raw = r.get(_skey(sid))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        expires_at = datetime.fromisoformat(data["expires_at"])
    except (ValueError, KeyError):
        return None
    if expires_at <= datetime.now(timezone.utc):
        delete_session(sid)
        return None
    # An invalidated (replaced) session must never continue, even in a login race.
    if r.get(_active_key(data["staff_id"])) != sid:
        r.delete(_skey(sid))
        return None
    r.expire(_skey(sid), int(IDLE_TIMEOUT.total_seconds()))  # sliding idle window
    return data


def delete_session(sid: str) -> None:
    r = client()
    raw = r.get(_skey(sid))
    if raw:
        try:
            staff_id = json.loads(raw)["staff_id"]
            if r.get(_active_key(staff_id)) == sid:
                r.delete(_active_key(staff_id))
        except (ValueError, KeyError):
            pass
    r.delete(_skey(sid))


def invalidate_staff_sessions(staff_id: str) -> None:
    r = client()
    sid = r.get(_active_key(staff_id))
    if sid:
        r.delete(_skey(sid))
    r.delete(_active_key(staff_id))


# ---------------------------------------------------------------- MFA email OTP

def _otp_hash(challenge_id: str, otp: str) -> str:
    return hashlib.sha256(f"{challenge_id}:{otp}".encode()).hexdigest()


def _new_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def create_challenge(staff_id: str) -> tuple[str, str]:
    cid = secrets.token_urlsafe(24)
    otp = _new_otp()
    client().set(
        f"inst_mfa:{cid}",
        json.dumps(
            {
                "staff_id": staff_id,
                "otp_hash": _otp_hash(cid, otp),
                "attempts": 0,
                "issued_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        ex=int(MFA_TTL.total_seconds()),
    )
    return cid, otp


def reissue_otp(cid: str) -> tuple[str, str] | None:
    """Replace the OTP (invalidating the old one). Returns (staff_id, otp), or
    ("COOLDOWN", "") while the resend cooldown is active, or None if unknown."""
    r = client()
    raw = r.get(f"inst_mfa:{cid}")
    if not raw:
        return None
    data = json.loads(raw)
    issued = datetime.fromisoformat(data["issued_at"])
    if datetime.now(timezone.utc) - issued < MFA_RESEND_COOLDOWN:
        return "COOLDOWN", ""
    otp = _new_otp()
    data.update(
        otp_hash=_otp_hash(cid, otp),
        attempts=0,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )
    r.set(f"inst_mfa:{cid}", json.dumps(data), ex=int(MFA_TTL.total_seconds()))
    return data["staff_id"], otp


def verify_otp(cid: str, otp: str) -> str | None:
    """Return staff_id on success. Consumes the challenge on success or after the
    5th failed attempt."""
    r = client()
    key = f"inst_mfa:{cid}"
    raw = r.get(key)
    if not raw:
        return None
    data = json.loads(raw)
    if data["attempts"] >= MFA_MAX_ATTEMPTS:
        r.delete(key)
        return None
    if hmac.compare_digest(data["otp_hash"], _otp_hash(cid, otp.strip())):
        r.delete(key)  # single use
        return data["staff_id"]
    data["attempts"] += 1
    if data["attempts"] >= MFA_MAX_ATTEMPTS:
        r.delete(key)
    else:
        r.set(key, json.dumps(data), keepttl=True)
    return None
