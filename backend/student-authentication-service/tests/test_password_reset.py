"""Forgot-password / reset-password through the real endpoints, on an in-memory SQLite DB and fake Redis.

Never touches the configured DATABASE_URL (the previous version of this test changed a real student's password in whatever database
the service was pointed at). The email is captured from the console backend instead of being sent."""
import hashlib
import re
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth as auth_module
from app.config import settings
from app.main import app
from app.models.password_reset_token import PasswordSetupResetToken
from app.models.student import Student
from app.routes import auth as auth_routes
from app.services import email as email_service
from app.services.password import hash_password, verify_password


@compiles(INET, "sqlite")
def _inet_sqlite(type_, compiler, **kw):
    return "VARCHAR(45)"


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Student.__table__.create(engine)
    PasswordSetupResetToken.__table__.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth_routes, "redis_client", fake)
    monkeypatch.setattr(auth_module, "redis_client", fake)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[auth_routes.get_db] = override
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "email_backend", "console")
    monkeypatch.setattr(auth_routes, "send_password_reset_email", lambda recipient_email, reset_url: sent.append((recipient_email, reset_url)))
    db = Session()
    db.add(Student(student_id="STU000001", first_name="Asha", last_name="Rao", email="asha@example.com",
                   password_hash=hash_password("Old-password-123"), status="ACTIVE"))
    db.commit()
    with TestClient(app) as client:
        yield client, Session, sent
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def _token(sent) -> str:
    return re.search(r"token=([A-Za-z0-9_\-]+)", sent[-1][1]).group(1)


def test_reset_flow_sets_new_password_and_token_is_single_use(env):
    client, Session, sent = env
    r = client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    assert r.status_code == 200 and sent and sent[-1][0] == "asha@example.com"
    raw = _token(sent)
    db = Session()
    token = db.scalar(select(PasswordSetupResetToken))
    assert token.token_hash == hashlib.sha256(raw.encode()).hexdigest()   # only the hash is stored
    assert token.student_id == "STU000001" and token.token_type == "PASSWORD_RESET"

    assert client.post("/auth/reset-password", json={"token": raw, "new_password": "New-password-456"}).status_code == 200
    db.expire_all()
    assert verify_password("New-password-456", db.get(Student, "STU000001").password_hash)
    assert db.scalar(select(PasswordSetupResetToken)).used_at is not None
    # single use
    assert client.post("/auth/reset-password", json={"token": raw, "new_password": "Another-pass-789"}).status_code == 400
    assert client.post("/auth/login", json={"login": "STU000001", "password": "New-password-456"}).status_code == 200


def test_unknown_email_gets_the_same_generic_answer_and_no_email(env):
    client, _, sent = env
    known = client.post("/auth/forgot-password", json={"email": "asha@example.com"}).json()
    unknown = client.post("/auth/forgot-password", json={"email": "nobody@example.com"}).json()
    assert known == unknown and len(sent) == 1


def test_expired_and_forged_tokens_are_rejected(env):
    client, Session, sent = env
    client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    raw = _token(sent)
    db = Session()
    t = db.scalar(select(PasswordSetupResetToken))
    t.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    assert client.post("/auth/reset-password", json={"token": raw, "new_password": "New-password-456"}).status_code == 400
    assert client.post("/auth/reset-password", json={"token": "forged", "new_password": "New-password-456"}).status_code == 400
    db.expire_all()
    assert verify_password("Old-password-123", db.get(Student, "STU000001").password_hash)


def test_a_new_request_invalidates_the_previous_link(env):
    client, _, sent = env
    client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    first = _token(sent)
    client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    second = _token(sent)
    assert client.post("/auth/reset-password", json={"token": first, "new_password": "New-password-456"}).status_code == 400
    assert client.post("/auth/reset-password", json={"token": second, "new_password": "New-password-456"}).status_code == 200


def test_console_backend_is_refused_outside_dev():
    from pydantic import ValidationError

    from app.config import Settings
    base = dict(database_url="sqlite://", cors_origins="x", session_cookie_secure=True, redis_url="redis://x", frontend_reset_url="x")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prod", email_backend="console", **base)
    assert Settings(_env_file=None, app_env="dev", email_backend="console", **base).email_backend == "console"
    assert email_service.email_enabled() in (True, False)
