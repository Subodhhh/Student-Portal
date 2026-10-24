"""Staff-initiated password setup link (internal, service key): the same token mechanism as forgot-password, at most 3 per
student per hour, and independent of the public per-IP forgot-password limit. In-memory SQLite and fake Redis.
Run with dummy env, e.g.:  DATABASE_URL=sqlite:// REDIS_URL=redis://x CORS_ORIGINS=x ..."""
import re

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.main import app
from app.models.password_reset_token import PasswordSetupResetToken
from app.models.student import Student
from app.routes import internal

KEY = "test-internal-key"
H = {"X-Service-Key": KEY}


@compiles(INET, "sqlite")
def _inet_sqlite(type_, compiler, **kw):
    return "VARCHAR(45)"


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Student.__table__.create(engine)
    PasswordSetupResetToken.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False)
    with session() as s:
        s.add(Student(student_id="STU000104", first_name="Harshal", last_name="P", email="h@example.com", status="ACTIVE"))
        s.add(Student(student_id="STU000105", first_name="Shreyash", last_name="S", email="s@example.com", status="ACTIVE"))
        s.commit()
    fake = fakeredis.FakeRedis(decode_responses=True)
    sent = []
    monkeypatch.setattr(internal, "SessionLocal", session)
    monkeypatch.setattr(internal, "redis_client", fake)
    monkeypatch.setattr(settings, "institute_service_api_keys", KEY)
    monkeypatch.setattr(settings, "email_backend", "console")
    monkeypatch.setattr("app.services.email.send_password_reset_email", lambda recipient_email, reset_url: sent.append((recipient_email, reset_url)))
    with TestClient(app) as c:
        yield c, session, fake, sent


def link(c, sid, headers=H):
    return c.post(f"/internal/v1/students/{sid}/password-setup-link", headers=headers)


def tokens(session, sid):
    with session() as s:
        return s.scalars(select(PasswordSetupResetToken).where(PasswordSetupResetToken.student_id == sid)).all()


def test_requires_the_service_key(env):
    c, session, _fake, sent = env
    assert link(c, "STU000104", headers={}).status_code == 401
    assert link(c, "STU000104", headers={"X-Service-Key": "wrong"}).status_code == 401
    assert tokens(session, "STU000104") == [] and sent == []


def test_unknown_student_is_404(env):
    c, _session, _fake, sent = env
    assert link(c, "STU999999").status_code == 404 and sent == []


def test_issues_a_reset_token_and_sends_the_link_without_returning_the_token(env):
    c, session, _fake, sent = env
    r = link(c, "STU000104")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"student_id", "expires_at"}                       # the token is never returned
    (tok,) = tokens(session, "STU000104")
    assert tok.token_type == "PASSWORD_RESET" and tok.used_at is None
    assert sent and sent[-1][0] == "h@example.com" and re.search(re.escape(settings.frontend_reset_url) + r"\?token=[A-Za-z0-9_\-]+", sent[-1][1])
    assert tok.token_hash not in sent[-1][1]                                    # only the hash is stored


def test_a_new_link_invalidates_the_previous_one(env):
    c, session, _fake, _sent = env
    link(c, "STU000104")
    link(c, "STU000104")
    rows = tokens(session, "STU000104")
    assert len(rows) == 2 and sum(t.used_at is None for t in rows) == 1


def test_at_most_three_links_per_student_per_hour(env):
    c, session, fake, sent = env
    assert [link(c, "STU000104").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert len(tokens(session, "STU000104")) == 3 and len(sent) == 3          # the refused request created nothing
    assert 0 < fake.ttl("auth_setup_link:STU000104") <= 3600
    assert link(c, "STU000105").status_code == 200                             # per student: another student is unaffected


def test_the_public_forgot_password_ip_limit_is_not_used(env):
    c, _session, fake, _sent = env
    for _ in range(3):
        link(c, "STU000104")
    assert not [k for k in fake.scan_iter("auth_forgot_password:*")]           # the public per-IP counter is untouched
