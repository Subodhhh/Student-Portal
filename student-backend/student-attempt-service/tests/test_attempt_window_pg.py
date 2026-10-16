"""Availability window, resume and server-side expiry, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_attempt_window_pg.py

Uses the real SQL (NOW() in the session time zone, like the test service's listings). Rows are unique per test."""
import json
import os
import re
import uuid

import fakeredis
import pytest
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
    from app.database import get_db
    from app.main import app
    from app.services import expiry

    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(expiry, "SessionLocal", Session)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth, "redis_client", fake)
    run = uuid.uuid4().hex[:8]
    sid = f"STW{run[:6].upper()}"
    fake.set("student_session:sess-" + run, json.dumps({"student_id": sid, "expires_at": "2999-01-01T00:00:00+00:00"}))
    p, b = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                       "VALUES (:s, 'Win', 'Dow', :e, 'ACTIVE', now(), now())"), {"s": sid, "e": f"w-{run}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) "
                       "VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"), {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) "
                       "VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"), {"b": b, "p": p})

    def make_test(start_offset: str, end_offset: str, duration=30) -> uuid.UUID:
        tid = uuid.uuid4()
        with engine.begin() as c:
            c.execute(text(f"""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks,
                               start_at, end_at, status, created_at, updated_at)
                               VALUES (:t, :p, :b, 'W', :d, 1, 4, now() + interval '{start_offset}', now() + interval '{end_offset}',
                                       'ACTIVE', now(), now())"""), {"t": tid, "p": p, "b": b, "d": duration})
            c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, question_text, question_type, option_a,
                              option_b, option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                              VALUES (:q, :t, 1, 'Q', 'MCQ', 'a', 'b', 'c', 'd', 'A', 4, 1, now(), now())"""),
                      {"q": uuid.uuid4(), "t": tid})
            c.execute(text("INSERT INTO test_audiences (test_audience_id, test_id, audience_type, student_id, created_at) "
                           "VALUES (:a, :t, 'STUDENT', :s, now())"), {"a": uuid.uuid4(), "t": tid, "s": sid})
        return tid

    with TestClient(app, cookies={"session_id": "sess-" + run}) as client:
        yield client, engine, make_test, sid, expiry
    app.dependency_overrides.clear()


def test_a_new_attempt_cannot_start_before_the_window_opens(env):
    client, _, make_test, *_ = env
    tid = make_test("1 hour", "2 hours")
    r = client.post("/attempts/start", json={"test_id": str(tid)})
    assert r.status_code == 400 and "not opened" in r.json()["detail"]


def test_a_new_attempt_cannot_start_after_the_window_closed(env):
    client, _, make_test, *_ = env
    tid = make_test("-2 hours", "-1 hour")
    r = client.post("/attempts/start", json={"test_id": str(tid)})
    assert r.status_code == 400 and "closed" in r.json()["detail"]


def test_an_attempt_started_in_the_window_resumes_after_it_closes_with_its_full_duration(env):
    client, engine, make_test, *_ = env
    tid = make_test("-10 minutes", "5 minutes", duration=60)
    first = client.post("/attempts/start", json={"test_id": str(tid)})
    assert first.status_code == 200, first.text
    with engine.begin() as c:   # the window closes while the student is disconnected
        c.execute(text("UPDATE tests SET end_at = now() - interval '1 minute' WHERE test_id = :t"), {"t": tid})
    again = client.post("/attempts/start", json={"test_id": str(tid)})
    assert again.status_code == 200 and again.json()["attempt_id"] == first.json()["attempt_id"]
    ws = client.get(f"/attempts/{first.json()['attempt_id']}/workspace").json()
    assert ws["remaining_seconds"] > 55 * 60   # full duration, not cut at the window end


def test_expired_attempts_are_finalised_by_the_server_sweep(env):
    client, engine, make_test, sid, expiry = env
    tid = make_test("-10 minutes", "1 hour")
    aid = client.post("/attempts/start", json={"test_id": str(tid)}).json()["attempt_id"]
    with engine.begin() as c:
        c.execute(text("UPDATE test_attempts SET expires_at = now() - interval '1 second' WHERE attempt_id = :a"), {"a": aid})
    assert expiry.finalize_expired() >= 1
    with engine.connect() as c:
        status, submitted, expires = c.execute(text("SELECT status::text, submitted_at, expires_at FROM test_attempts WHERE attempt_id = :a"),
                                               {"a": aid}).one()
    assert status == "AUTO_SUBMITTED" and submitted == expires
    assert expiry.finalize_expired() == 0   # idempotent: finished attempts are never touched again
    r = client.post("/attempts/start", json={"test_id": str(tid)})
    assert r.status_code == 400   # one attempt per exam


def test_a_student_outside_the_audience_cannot_start(env):
    client, engine, make_test, *_ = env
    tid = make_test("-10 minutes", "1 hour")
    with engine.begin() as c:
        c.execute(text("DELETE FROM test_audiences WHERE test_id = :t"), {"t": tid})
    assert client.post("/attempts/start", json={"test_id": str(tid)}).status_code == 403
