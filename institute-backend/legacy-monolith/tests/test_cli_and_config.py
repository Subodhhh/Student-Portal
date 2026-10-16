import pytest
from sqlalchemy import select

from app import cli
from app.config import Settings
from app.core.roles import Role
from app.models.staff import Staff, StaffRoleAssignment


def test_cli_creates_super_admin_who_still_needs_mfa(client, db_factory, monkeypatch, outbox):
    monkeypatch.setattr(cli, "SessionLocal", db_factory)
    code = cli.create_super_admin("root@example.com", "Root Admin", "a long operator passphrase")
    assert code == "STF000001"
    with db_factory() as db:
        s = db.scalar(select(Staff))
        assert s.password_hash.startswith("$argon2id$")
        assert db.scalar(select(StaffRoleAssignment.role)) == Role.SUPER_ADMIN.value
    r = client.post("/api/v1/auth/login", json={"staff_code": code, "password": "a long operator passphrase"},
                    headers={"Origin": "http://localhost:5174"})
    assert r.json()["status"] == "MFA_REQUIRED"


def test_cli_rejects_weak_password_and_duplicates(db_factory, monkeypatch):
    monkeypatch.setattr(cli, "SessionLocal", db_factory)
    with pytest.raises(SystemExit):
        cli.create_super_admin("root@example.com", "Root Admin", "short")
    cli.create_super_admin("root@example.com", "Root Admin", "a long operator passphrase")
    with pytest.raises(SystemExit):
        cli.create_super_admin("root@example.com", "Root Admin", "a long operator passphrase")


BASE = dict(database_url="postgresql://x", redis_url="redis://x", cors_origins="https://portal.example.com")


def test_prod_config_must_be_secure():
    assert Settings(**BASE, app_env="prod", session_cookie_secure=True, email_backend="smtp")
    for bad in (dict(session_cookie_secure=False, email_backend="smtp"),
                dict(session_cookie_secure=True, email_backend="console"),
                dict(session_cookie_secure=True, email_backend="smtp", cors_origins="http://portal.example.com")):
        with pytest.raises(ValueError):
            Settings(**{**BASE, **bad}, app_env="prod")


def test_cors_wildcard_and_samesite_none_rejected():
    with pytest.raises(ValueError):
        Settings(**{**BASE, "cors_origins": "*"})
    with pytest.raises(ValueError):
        Settings(**BASE, session_cookie_samesite="none", session_cookie_secure=False)
    assert Settings(**BASE, session_cookie_samesite="none", session_cookie_secure=True)
