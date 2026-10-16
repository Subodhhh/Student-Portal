"""Profile updates on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_profile_pg.py"""
import json
import os
import re
import uuid

import fakeredis
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")


@pytest.fixture()
def env(monkeypatch):
    from app import auth
    from app.main import app
    from app.routes import profile

    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[profile.get_db] = override
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth, "redis_client", fake)
    run = uuid.uuid4().hex[:8]
    sid, other = f"STP{run[:6].upper()}", f"STQ{run[:6].upper()}"
    fake.set("student_session:p-" + run, json.dumps({"student_id": sid, "expires_at": "2999-01-01T00:00:00+00:00"}))
    with engine.begin() as c:
        for s, e in ((sid, f"me-{run}@example.com"), (other, f"taken-{run}@example.com")):
            c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, password_hash, status, created_at, updated_at) "
                           "VALUES (:s, 'P', 'R', :e, :h, 'ACTIVE', now(), now())"), {"s": s, "e": e, "h": PasswordHasher().hash("Right-password-1")})
    with TestClient(app, cookies={"session_id": "p-" + run}) as client:
        yield client, run
    app.dependency_overrides.clear()


def body(run, **kw):
    return {"first_name": "Asha", "last_name": "Rao", "email": f"me-{run}@example.com", **kw}


def test_name_changes_do_not_need_the_password(env):
    client, run = env
    r = client.put("/profile", json=body(run, first_name="Renamed"))
    assert r.status_code == 200 and r.json()["first_name"] == "Renamed"


def test_email_change_requires_the_current_password(env):
    client, run = env
    assert client.put("/profile", json=body(run, email=f"new-{run}@example.com")).status_code == 403
    assert client.put("/profile", json=body(run, email=f"new-{run}@example.com", current_password="wrong")).status_code == 403
    r = client.put("/profile", json=body(run, email=f"New-{run}@Example.com", current_password="Right-password-1"))
    assert r.status_code == 200 and r.json()["email"] == f"new-{run}@example.com"   # stored lower-case


def test_taking_another_students_email_is_a_clean_conflict(env):
    client, run = env
    r = client.put("/profile", json=body(run, email=f"taken-{run}@example.com", current_password="Right-password-1"))
    assert r.status_code == 409
