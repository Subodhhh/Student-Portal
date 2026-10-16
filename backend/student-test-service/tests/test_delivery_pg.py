"""Delivery and resume rules on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_delivery_pg.py"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")
KEY = "k-deliver"


@pytest.fixture()
def env(monkeypatch):
    from app import auth
    from app.config import settings
    from app.database import get_db
    from app.main import app

    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "institute_service_api_keys", KEY)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth, "redis_client", fake)
    run = uuid.uuid4().hex[:8]
    sid = f"STD{run[:6].upper()}"
    fake.set("student_session:d-" + run, json.dumps({"student_id": sid}))
    p, b = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                       "VALUES (:s, 'D', 'L', :e, 'ACTIVE', now(), now())"), {"s": sid, "e": f"d-{run}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"pd-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})
    with TestClient(app, cookies={"session_id": "d-" + run}) as client:
        yield client, engine, sid, p, b
    app.dependency_overrides.clear()


def exam(p, b, sid, start, end):
    return {"operation": "PUBLISH", "exam_version_id": str(uuid.uuid4()), "provider_id": str(p), "branch_id": str(b), "name": "Delivered",
            "duration_minutes": 60, "start_at": start.isoformat(), "end_at": end.isoformat(), "student_ids": [sid],
            "questions": [{"order": 1, "subject": "Physics", "text": "Q", "options": {"A": "a", "B": "b", "C": "c", "D": "d"},
                           "correct": "A", "marks": 4, "negative_marks": 1}]}


def test_unknown_provider_is_a_retryable_reference_missing(env):
    client, engine, sid, p, b = env
    now = datetime.now(timezone.utc)
    body = exam(uuid.uuid4(), b, sid, now, now + timedelta(hours=1))
    r = client.post("/internal/v1/exams", json=body, headers={"X-Service-Key": KEY})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "REFERENCE_MISSING"
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM tests WHERE test_id = :t"), {"t": body["exam_version_id"]}).scalar() == 0


def test_paper_can_be_loaded_to_resume_after_the_window_closed_but_not_to_start(env):
    client, engine, sid, p, b = env
    now = datetime.now(timezone.utc)
    body = exam(p, b, sid, now - timedelta(minutes=5), now + timedelta(minutes=30))
    assert client.post("/internal/v1/exams", json=body, headers={"X-Service-Key": KEY}).status_code == 201
    tid = body["exam_version_id"]
    assert client.get(f"/tests/{tid}/start").status_code == 200
    with engine.begin() as c:
        c.execute(text("UPDATE tests SET end_at = now() - interval '1 minute' WHERE test_id = :t"), {"t": tid})
    assert client.get(f"/tests/{tid}/start").status_code == 404            # closed and never started: no paper
    with engine.begin() as c:
        c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, status)
                          VALUES (:a, :t, :s, now() - interval '10 minutes', now() + interval '50 minutes', 'IN_PROGRESS')"""),
                  {"a": uuid.uuid4(), "t": tid, "s": sid})
    r = client.get(f"/tests/{tid}/start")
    assert r.status_code == 200 and len(r.json()["questions"]) == 1        # running attempt: resume allowed
    assert "correct_answer" not in r.json()["questions"][0]                # the key is never sent to the browser


def test_a_student_session_cannot_touch_test_audiences(env):
    client, engine, sid, p, b = env
    tid = str(uuid.uuid4())
    body = {"audience_type": "STUDENT", "student_id": sid, "provider_id": str(p)}
    assert client.post(f"/tests/{tid}/audiences", json=body).status_code == 401      # session cookie alone is not enough
    assert client.get(f"/tests/{tid}/audiences").status_code == 401
