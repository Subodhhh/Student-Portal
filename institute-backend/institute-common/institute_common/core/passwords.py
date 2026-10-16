"""Argon2id hashing and the backend password policy."""
import hashlib
import urllib.request

from argon2 import PasswordHasher, Type

from institute_common.config import settings

MIN_LENGTH = 12
MAX_LENGTH = 128

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost_kib,
    parallelism=settings.argon2_parallelism,
    type=Type.ID,
)
# Verified when the account is unknown so timing does not reveal existence.
_DUMMY_HASH = _hasher.hash("institute-portal-dummy-password")

# Small offline denylist; HIBP k-anonymity lookup is an optional extra layer.
COMMON_PASSWORDS = {
    "password1234", "passwordpassword", "123456789012", "1234567890123", "qwertyuiop12",
    "qwertyuiopas", "iloveyou1234", "administrator", "welcome12345", "letmein123456",
    "changeme1234", "password@123", "password123456", "admin1234567", "abcdefghijkl",
    "abc123456789", "111111111111", "000000000000", "123123123123", "neetaiims1234",
    "institute1234", "student12345", "teacher12345", "welcome@12345", "passw0rd1234",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
        return password_hash is not None
    except Exception:
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def _hibp_pwned(password: str) -> bool:
    sha1 = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        req = urllib.request.Request(
            f"https://api.pwnedpasswords.com/range/{prefix}", headers={"Add-Padding": "true"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 (fixed https URL)
            body = resp.read().decode()
    except Exception:
        return False  # fail open on network error; the offline list still applies
    return any(
        line.split(":")[0] == suffix and int(line.split(":")[1]) > 0 for line in body.splitlines()
    )


def validate_password(password: str, *, personal: list[str] | None = None) -> list[str]:
    """Return a list of problems (empty = acceptable). No composition rules."""
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        problems.append(f"Password must be at most {MAX_LENGTH} characters.")
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) <= 2:
        problems.append("Password is too common or too repetitive.")
    for item in personal or []:
        if item and len(item) >= 4 and item.lower() in lowered:
            problems.append("Password must not contain your name, email or Staff ID.")
            break
    if not problems and settings.hibp_enabled and _hibp_pwned(password):
        problems.append("Password appears in known data breaches.")
    return problems
