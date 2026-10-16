"""Tests for the internal student API. They use an in-memory SQLite DB and fake Redis and
never touch the configured DATABASE_URL (unlike the older tests in this folder).
Run with dummy env, e.g.:  DATABASE_URL=sqlite:// REDIS_URL=redis://x CORS_ORIGINS=x ..."""
import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.main import app
from app.models.student import Student
from app.routes import internal

KEY = "test-internal-key"


@compiles(INET, "sqlite")
def _inet_sqlite(type_, compiler, **kw):
    return "VARCHAR(45)"


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Student.__table__.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(internal, "SessionLocal", Session)
    monkeypatch.setattr(internal, "redis_client", fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr(settings, "institute_service_api_keys", KEY)
    with TestClient(app) as c:
        yield c


H = {"X-Service-Key": KEY}
NEW = {"first_name": "Asha", "last_name": "Rao", "email": "Asha@Example.com", "phone": "9000000001"}


def test_requires_valid_key(client):
    assert client.post("/internal/v1/students/search", json={"email": "a@b.co"}).status_code == 401
    assert client.post("/internal/v1/students/search", json={"email": "a@b.co"}, headers={"X-Service-Key": "nope"}).status_code == 401
    assert client.post("/internal/v1/students/search", json={"email": "a@b.co"}, headers=H).status_code == 200


def test_disabled_when_no_key_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert client.post("/internal/v1/students/search", json={"email": "a@b.co"}, headers=H).status_code == 404


def test_session_cookie_is_not_a_credential(client):
    client.cookies.set("session_id", "whatever")
    assert client.post("/internal/v1/students/search", json={"email": "a@b.co"}).status_code == 401


def test_rotation_overlap_accepts_either_key(client, monkeypatch):
    monkeypatch.setattr(settings, "institute_service_api_keys", f"old-key,{KEY}")
    for k in ("old-key", KEY):
        assert client.post("/internal/v1/students/search", json={"email": "a@b.co"}, headers={"X-Service-Key": k}).status_code == 200


def test_create_is_idempotent_and_ids_are_sequential(client):
    h = {**H, "Idempotency-Key": "k1"}
    a = client.post("/internal/v1/students", json=NEW, headers=h)
    assert a.status_code == 201 and a.json()["student_id"] == "STU000001" and a.json()["email"] == "asha@example.com"
    retry = client.post("/internal/v1/students", json=NEW, headers=h)
    assert retry.status_code == 201 and retry.json()["student_id"] == "STU000001"
    other = client.post("/internal/v1/students", json={**NEW, "email": "b@example.com"}, headers={**H, "Idempotency-Key": "k2"})
    assert other.json()["student_id"] == "STU000002"


def test_duplicate_email_is_conflict_and_idempotency_key_required(client):
    client.post("/internal/v1/students", json=NEW, headers={**H, "Idempotency-Key": "k1"})
    dup = client.post("/internal/v1/students", json=NEW, headers={**H, "Idempotency-Key": "k9"})
    assert dup.status_code == 409
    assert client.post("/internal/v1/students", json={**NEW, "email": "c@example.com"}, headers=H).status_code == 422


def test_search_get_and_batch(client):
    client.post("/internal/v1/students", json=NEW, headers={**H, "Idempotency-Key": "k1"})
    assert [s["student_id"] for s in client.post("/internal/v1/students/search", json={"email": "asha@example.com"}, headers=H).json()] == ["STU000001"]
    assert client.post("/internal/v1/students/search", json={"phone": "9000000001"}, headers=H).json()[0]["student_id"] == "STU000001"
    assert client.post("/internal/v1/students/search", json={}, headers=H).status_code == 422
    assert client.get("/internal/v1/students/STU000001", headers=H).json()["first_name"] == "Asha"
    assert client.get("/internal/v1/students/STU999999", headers=H).status_code == 404
    assert len(client.post("/internal/v1/students/batch-get", json={"student_ids": ["STU000001", "x"]}, headers=H).json()) == 1


def test_malformed_email_rejected_without_email_validator_dependency(client):
    """The service's pinned requirements have no email-validator: validation is a plain pattern."""
    for bad in ("not-an-email", "a@b", "a b@c.io", "@c.io"):
        assert client.post("/internal/v1/students", json={**NEW, "email": bad}, headers={**H, "Idempotency-Key": "kx"}).status_code == 422
        assert client.post("/internal/v1/students/search", json={"email": bad}, headers=H).status_code == 422
    assert "email_validator" not in __import__("sys").modules


def test_malformed_email_rejected_without_email_validator_dependency(client):
    """The service's pinned requirements have no email-validator: validation is a plain pattern."""
    for bad in ("not-an-email", "a@b", "a b@c.io", "@c.io"):
        assert client.post("/internal/v1/students", json={**NEW, "email": bad}, headers={**H, "Idempotency-Key": "kx"}).status_code == 422
        assert client.post("/internal/v1/students/search", json={"email": bad}, headers=H).status_code == 422
    assert "email_validator" not in __import__("sys").modules
