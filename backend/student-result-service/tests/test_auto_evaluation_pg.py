"""Automatic evaluation and answer-review gating, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_auto_evaluation_pg.py"""
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
    from app.services import auto_evaluation

    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(auto_evaluation, "SessionLocal", Session)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth, "redis_client", fake)
    run = uuid.uuid4().hex[:8]
    sid = f"STE{run[:6].upper()}"
    fake.set("student_session:s-" + run, json.dumps({"student_id": sid}))
    p, b, tid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    q = [uuid.uuid4() for _ in range(3)]
    aid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"pe-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, created_at, updated_at) VALUES (:t, :p, :b, 'E', 30, 3, 12, now() - interval '1 hour', now() + interval '1 hour',
                          'ACTIVE', now(), now())"""), {"t": tid, "p": p, "b": b})
        for i, (qid, key) in enumerate(zip(q, "ABC"), start=1):
            c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, question_text, question_type, option_a, option_b, option_c,
                              option_d, correct_answer, marks, negative_marks, subject, created_at, updated_at)
                              VALUES (:q, :t, :o, 'Q', 'MCQ', 'a', 'b', 'c', 'd', :k, 4, 1, 'Physics', now(), now())"""),
                      {"q": qid, "t": tid, "o": i, "k": key})
        # The browser was closed: the attempt was auto-submitted by the attempt service's sweep and nobody asked for evaluation.
        c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status)
                          VALUES (:a, :t, :s, now() - interval '40 minutes', now() - interval '10 minutes', now() - interval '10 minutes',
                                  'AUTO_SUBMITTED')"""), {"a": aid, "t": tid, "s": sid})
        for qid, ans in ((q[0], "A"), (q[1], "D")):   # one right, one wrong, one unanswered
            c.execute(text("""INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, question_state, updated_at)
                              VALUES (:i, :a, :q, :s, 'ANSWERED', now())"""), {"i": uuid.uuid4(), "a": aid, "q": qid, "s": ans})
    with TestClient(app, cookies={"session_id": "s-" + run}) as client:
        yield client, engine, aid, tid, auto_evaluation
    app.dependency_overrides.clear()


def test_auto_submitted_attempts_are_evaluated_without_the_browser(env):
    client, engine, aid, tid, auto_evaluation = env
    assert client.get(f"/results/attempts/{aid}").status_code == 404
    stats = auto_evaluation.evaluate_pending()
    assert stats["evaluated"] >= 1
    r = client.get(f"/results/attempts/{aid}").json()
    assert (r["obtained_marks"], r["total_marks"], r["correct_answers"], r["incorrect_answers"], r["unanswered_questions"]) == (3, 12, 1, 1, 1)
    auto_evaluation.evaluate_pending()   # a second pass must not create a second result for the same attempt
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM test_results WHERE attempt_id = :a"), {"a": aid}).scalar() == 1


def test_answer_review_opens_only_after_the_window_closes(env):
    client, engine, aid, tid, auto_evaluation = env
    auto_evaluation.evaluate_pending()
    d = client.get(f"/results/attempts/{aid}/details").json()
    assert d["review_available"] is False and d["questions"] == [] and d["review_available_at"]
    assert d["subjects"][0]["obtained_marks"] == 3        # the score breakdown is not hidden
    with engine.begin() as c:
        c.execute(text("UPDATE tests SET end_at = now() - interval '1 second' WHERE test_id = :t"), {"t": tid})
    d = client.get(f"/results/attempts/{aid}/details").json()
    assert d["review_available"] is True and len(d["questions"]) == 3
    assert {x["status"] for x in d["questions"]} == {"CORRECT", "INCORRECT", "UNANSWERED"}


def test_another_students_result_is_not_reachable(env):
    client, engine, aid, tid, auto_evaluation = env
    auto_evaluation.evaluate_pending()
    other = TestClient(client.app, cookies={"session_id": "nobody"})
    assert other.get(f"/results/attempts/{aid}").status_code == 401
