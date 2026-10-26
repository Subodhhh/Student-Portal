"""Which result counts after an authorized retake, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55433/stu_unified_verify pytest tests/test_official_result_pg.py

The original counts until the retake is evaluated; then the Institute sees only the retake's result (one per student), the student sees
both, labelled, and manual marks adjustments target the official result. Nothing is deleted."""
import os
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")
KEY = {"X-Service-Key": "k-off"}


@pytest.fixture()
def env(monkeypatch):
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
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-off")
    run = uuid.uuid4().hex[:6].upper()
    sid, p, b, tid = f"STO{run}", uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                       "VALUES (:s, 'Off', 'Icial', :e, 'ACTIVE', now(), now())"), {"s": sid, "e": f"o-{run}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, created_at, updated_at) VALUES (:t, :p, :b, 'Official', 30, 4, 16, now() - interval '2 hours', now() - interval '1 hour',
                          'ACTIVE', now(), now())"""), {"t": tid, "p": p, "b": b})

    def attempt(retake_of=None, marks=None, reason=None):
        a = uuid.uuid4()
        with engine.begin() as c:
            c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status, submit_reason,
                              retake_of_attempt_id) VALUES (:a, :t, :s, now(), now() + interval '1 hour', now(), 'AUTO_SUBMITTED', :r, :o)"""),
                      {"a": a, "t": tid, "s": sid, "r": reason, "o": retake_of})
            if marks is not None:
                c.execute(text("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions,
                                  correct_answers, incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, created_at)
                                  VALUES (:r, :a, :t, :s, 4, 4, 0, 0, 0, 16, :m, 0, now())"""), {"r": uuid.uuid4(), "a": a, "t": tid, "s": sid, "m": marks})
        return a

    with TestClient(app) as c:
        yield c, Session, tid, sid, attempt
    app.dependency_overrides.clear()


def test_the_original_counts_until_the_retake_is_evaluated_then_only_the_retake(env):
    c, Session, tid, sid, attempt = env
    from app.repositories.result_repository import get_student_results
    original = attempt(marks=11, reason="WARNING_THRESHOLD")
    retake = attempt(retake_of=original)                                   # authorized and submitted, not yet evaluated
    assert [r["obtained_marks"] for r in c.get(f"/internal/v1/tests/{tid}/results", headers=KEY).json()] == [11]
    with Session() as db:                                                  # the retake is evaluated
        db.execute(text("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions,
                           correct_answers, incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, created_at)
                           VALUES (:r, :a, :t, :s, 4, 4, 0, 0, 0, 16, 7, 0, now())"""), {"r": uuid.uuid4(), "a": retake, "t": tid, "s": sid})
        db.commit()
    assert [r["obtained_marks"] for r in c.get(f"/internal/v1/tests/{tid}/results", headers=KEY).json()] == [7]   # one per student
    with Session() as db:
        mine = sorted((r["obtained_marks"], r["is_retake"], r["superseded"]) for r in get_student_results(db, sid) if r["test_name"] == "Official")
    assert mine == [(7, True, False), (11, False, True)]                  # both kept and labelled for the student
    adj = c.post("/internal/v1/results/adjust", headers=KEY, json={"test_id": str(tid), "student_id": sid, "obtained_marks": 8,
                                                                   "reason": "manual review", "actor": "STF000001", "correction_ref": "corr-retake-1"})
    assert adj.status_code == 200 and adj.json()["previous"]["obtained_marks"] == 7     # the official (retake) result, not the original
