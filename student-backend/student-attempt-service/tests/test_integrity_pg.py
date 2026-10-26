"""Exam integrity on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55433/stu_unified_verify pytest tests/test_integrity_pg.py

Server-side warning count with de-duplication and idempotent reports, automatic submission at the threshold (not resumable), the
student's review request, the provider's decision through the internal API, and an authorized retake as a NEW linked attempt that
never changes the original."""
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
KEY = {"X-Service-Key": "k-int"}


@pytest.fixture()
def env(monkeypatch):
    from app import auth
    from app.config import settings
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
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-int")
    monkeypatch.setattr(settings, "integrity_warning_threshold", 3)
    monkeypatch.setattr(settings, "integrity_debounce_seconds", 0.0)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth, "redis_client", fake)
    run = uuid.uuid4().hex[:6].upper()
    sid = f"STI{run}"
    p, b, tid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake.set(f"student_session:sess-{run}", json.dumps({"student_id": sid, "expires_at": "2999-01-01T00:00:00+00:00"}))
    with engine.begin() as c:
        c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                       "VALUES (:s, 'Int', 'Egrity', :e, 'ACTIVE', now(), now())"), {"s": sid, "e": f"i-{run}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, created_at, updated_at) VALUES (:t, :p, :b, 'Integrity', 30, 2, 8, now() - interval '1 hour', now() + interval '2 hours',
                          'ACTIVE', now(), now())"""), {"t": tid, "p": p, "b": b})
        for i in (1, 2):
            c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, question_text, question_type, option_a, option_b,
                              option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                              VALUES (:q, :t, :o, 'Physics', 'Q', 'MCQ', 'a', 'b', 'c', 'd', 'A', 4, 1, now(), now())"""),
                      {"q": uuid.uuid4(), "t": tid, "o": i})
        c.execute(text("INSERT INTO test_audiences (test_audience_id, test_id, audience_type, student_id, created_at) VALUES (:a, :t, 'STUDENT', :s, now())"),
                  {"a": uuid.uuid4(), "t": tid, "s": sid})
    with TestClient(app, cookies={"session_id": f"sess-{run}"}) as client:
        yield client, engine, tid, sid, settings
    app.dependency_overrides.clear()


def event(c, attempt_id, kind="TAB_HIDDEN", client_event_id=None):
    r = c.post(f"/attempts/{attempt_id}/integrity-events", json={"client_event_id": str(client_event_id or uuid.uuid4()), "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def start(c, tid):
    return c.post("/attempts/start", json={"test_id": str(tid)})


def test_three_counted_warnings_submit_the_attempt_automatically_and_it_cannot_be_resumed(env):
    c, engine, tid, sid, _s = env
    a = start(c, tid).json()["attempt_id"]
    q = c.get(f"/attempts/{a}/workspace").json()["questions"][0]["question_id"]
    assert c.post(f"/attempts/{a}/answers", json={"question_id": q, "selected_answer": "A"}).status_code == 200
    assert [event(c, a, k)["warnings"] for k in ("TAB_HIDDEN", "WINDOW_BLUR")] == [1, 2]
    third = event(c, a, "FULLSCREEN_EXIT")
    assert (third["warnings"], third["auto_submitted"], third["status"], third["submit_reason"], third["review_status"]) == (
        3, True, "AUTO_SUBMITTED", "WARNING_THRESHOLD", "UNDER_REVIEW")
    late = event(c, a, "TAB_HIDDEN")                                         # after submission: not recorded, never a 4th warning
    assert (late["recorded"], late["warnings"]) == (False, 3)
    assert c.post(f"/attempts/{a}/submit").status_code == 400                # no duplicate submission
    r = start(c, tid)
    assert r.status_code == 400 and "already attempted" in r.json()["detail"]   # not resumable like an interruption
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from test_attempts where test_id = :t"), {"t": tid}).scalar() == 1
        assert conn.execute(text("select count(*) from attempt_answers where attempt_id = :a"), {"a": a}).scalar() == 1   # answers kept
        kinds = conn.execute(text("select kind, warning_no from attempt_integrity_events where attempt_id = :a order by received_at"), {"a": a}).all()
    assert kinds == [("TAB_HIDDEN", 1), ("WINDOW_BLUR", 2), ("FULLSCREEN_EXIT", 3)]


def test_one_action_counts_once_and_a_retried_report_never_counts_twice(env, monkeypatch):
    c, _engine, tid, _sid, settings = env
    monkeypatch.setattr(settings, "integrity_debounce_seconds", 30.0)
    a = start(c, tid).json()["attempt_id"]
    first = event(c, a, "TAB_HIDDEN")
    blur = event(c, a, "WINDOW_BLUR")                                        # the same tab switch fires both events
    assert (first["counted"], blur["counted"], blur["recorded"], blur["warnings"]) == (True, False, True, 1)
    same = uuid.uuid4()
    monkeypatch.setattr(settings, "integrity_debounce_seconds", 0.0)
    assert event(c, a, client_event_id=same)["warnings"] == 2
    again = event(c, a, client_event_id=same)                                # network retry of the same report
    assert (again["recorded"], again["warnings"]) == (False, 2)
    assert c.get(f"/attempts/{a}/integrity").json()["warnings"] == 2          # the server's count is the only count


def test_reloading_and_resuming_never_creates_warnings_or_attempts(env):
    c, engine, tid, _sid, _s = env
    a = start(c, tid).json()["attempt_id"]
    for _ in range(3):
        assert c.get(f"/attempts/{a}/workspace").status_code == 200
        assert start(c, tid).json()["attempt_id"] == a
    st = c.get(f"/attempts/{a}/integrity").json()
    assert (st["warnings"], st["status"], st["threshold"]) == (0, "IN_PROGRESS", 3)
    assert c.post(f"/attempts/{a}/submit").json()["status"] == "SUBMITTED"
    with engine.connect() as conn:
        assert conn.execute(text("select submit_reason from test_attempts where attempt_id = :a"), {"a": a}).scalar() == "STUDENT"
    assert c.post(f"/attempts/{a}/review", json={"message": "please review my attempt"}).status_code == 400   # nothing to review


def test_review_request_decision_and_an_authorized_retake_as_a_new_linked_attempt(env):
    c, engine, tid, sid, _s = env
    a = start(c, tid).json()["attempt_id"]
    for _ in range(3):
        event(c, a)
    asked = c.post(f"/attempts/{a}/review", json={"message": "My browser showed a system update prompt."})
    assert asked.status_code == 200 and asked.json()["status"] == "OPEN"
    assert c.post(f"/attempts/{a}/review", json={"message": "a second, different message"}).json()["student_message"].startswith("My browser")
    assert start(c, tid).status_code == 400                                   # no retake before a decision
    detail = c.get(f"/internal/v1/attempts/{a}/integrity", headers=KEY).json()
    assert [e["warning_no"] for e in detail["events"]] == [1, 2, 3] and detail["review"]["status"] == "OPEN"
    staff = str(uuid.uuid4())
    body = {"decision": "RETAKE", "reason": "Verified: the prompt was an operating-system update.", "staff_id": staff, "staff_name": "Admin A"}
    assert c.post(f"/internal/v1/attempts/{a}/review-decision", json=body, headers=KEY).json()["status"] == "RETAKE_AUTHORIZED"
    assert c.post(f"/internal/v1/attempts/{a}/review-decision", json=body, headers=KEY).status_code == 200      # same decision: idempotent
    assert c.post(f"/internal/v1/attempts/{a}/review-decision", json={**body, "decision": "UPHOLD"}, headers=KEY).status_code == 409
    retake = start(c, tid).json()
    assert retake["attempt_id"] != a and retake["status"] == "IN_PROGRESS"
    assert start(c, tid).json()["attempt_id"] == retake["attempt_id"]          # resuming the retake, not a third attempt
    with engine.connect() as conn:
        rows = conn.execute(text("""select attempt_id::text, status, submit_reason, review_status, retake_of_attempt_id::text from test_attempts
                                    where test_id = :t order by started_at"""), {"t": tid}).all()
        decided = conn.execute(text("select decided_by_staff_id::text, decided_by_name, retake_attempt_id::text from attempt_reviews where attempt_id = :a"),
                               {"a": a}).one()
    assert rows == [(a, "AUTO_SUBMITTED", "WARNING_THRESHOLD", "RETAKE_AUTHORIZED", None),          # the original is never changed back
                    (retake["attempt_id"], "IN_PROGRESS", None, None, a)]
    assert decided == (staff, "Admin A", retake["attempt_id"])
    assert c.post(f"/attempts/{retake['attempt_id']}/submit").json()["status"] == "SUBMITTED"
    assert start(c, tid).status_code == 400                                   # the retake is used: no further attempt
    listed = c.get(f"/internal/v1/tests/{tid}/attempts", headers=KEY).json()
    assert [(x["attempt_id"], x["retake_of_attempt_id"], x["warnings"]) for x in listed] == [(a, None, 3), (retake["attempt_id"], a, 0)]


def test_an_upheld_submission_allows_no_retake_and_students_cannot_decide(env):
    c, _engine, tid, _sid, _s = env
    a = start(c, tid).json()["attempt_id"]
    for _ in range(3):
        event(c, a)
    body = {"decision": "UPHOLD", "reason": "Repeated tab switches confirmed.", "staff_id": str(uuid.uuid4()), "staff_name": "Admin B"}
    assert c.post(f"/internal/v1/attempts/{a}/review-decision", json=body).status_code == 401                 # no service key
    assert c.post(f"/internal/v1/attempts/{a}/review-decision", json=body, headers=KEY).json()["status"] == "UPHELD"
    assert start(c, tid).status_code == 400
