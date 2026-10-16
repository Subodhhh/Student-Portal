"""Run with: DATABASE_URL=sqlite:// pytest tests/test_internal_answer_key.py (in-memory, never the real DB)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import question as question_models
from app.models import test as test_models

H = {"X-Service-Key": "k-key"}


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-key")
    test_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session() as db:
        db.add(test_models.Test(test_id=test_id, provider_id=uuid.uuid4(), branch_id=uuid.uuid4(), name="T", duration_minutes=60, total_questions=2,
                                total_marks=8, start_at=now, end_at=now + timedelta(hours=1), status="ACTIVE"))
        db.flush()
        for order, correct in ((1, "A"), (2, "C")):
            db.add(question_models.Question(question_id=uuid.uuid4(), test_id=test_id, subject="Physics", question_order=order, question_text=f"Q{order}",
                                            question_type="MCQ", option_a="a", option_b="b", option_c="c", option_d="d", correct_answer=correct, marks=4, negative_marks=1))
        db.commit()
    with TestClient(app) as c:
        yield c, Session, test_id
    app.dependency_overrides.clear()


def body(**over):
    b = {"changes": [{"question_order": 2, "correct": "D"}], "reason": "Printed key was wrong", "actor": "STF000001", "correction_ref": "corr-0001-abcd"}
    b.update(over)
    return b


def keys(Session, test_id):
    with Session() as db:
        return {q.question_order: q.correct_answer for q in db.scalars(select(question_models.Question).where(question_models.Question.test_id == test_id))}


def test_requires_service_key_and_is_disabled_by_default(env, monkeypatch):
    c, _, t = env
    assert c.post(f"/internal/v1/exams/{t}/answer-key", json=body()).status_code == 401
    assert c.post(f"/internal/v1/exams/{t}/answer-key", json=body(), headers={"X-Service-Key": "x"}).status_code == 401
    c.cookies.set("session_id", "student")
    assert c.post(f"/internal/v1/exams/{t}/answer-key", json=body()).status_code == 401
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert c.post(f"/internal/v1/exams/{t}/answer-key", json=body(), headers=H).status_code == 404


def test_changes_the_key_and_reports_previous_values(env):
    c, Session, t = env
    r = c.post(f"/internal/v1/exams/{t}/answer-key", json=body(), headers=H)
    assert r.status_code == 200 and r.json()["changes"] == [{"question_order": 2, "previous": "C", "new": "D"}]
    assert keys(Session, t) == {1: "A", 2: "D"}


def test_replay_is_idempotent(env):
    c, Session, t = env
    c.post(f"/internal/v1/exams/{t}/answer-key", json=body(), headers=H)
    again = c.post(f"/internal/v1/exams/{t}/answer-key", json=body(), headers=H)
    assert again.status_code == 200 and again.json()["changes"] == [{"question_order": 2, "previous": "D", "new": "D", "unchanged": True}]
    assert keys(Session, t) == {1: "A", 2: "D"}


@pytest.mark.parametrize("mutate,status", [
    (lambda b: b.update(changes=[]), 422),
    (lambda b: b.update(changes=[{"question_order": 2, "correct": "Z"}]), 422),
    (lambda b: b.update(changes=[{"question_order": 99, "correct": "A"}]), 404),
    (lambda b: b.update(changes=[{"question_order": 1, "correct": "B"}, {"question_order": 1, "correct": "C"}]), 422),
    (lambda b: b.update(reason="x"), 422),
])
def test_invalid_requests_change_nothing(env, mutate, status):
    c, Session, t = env
    b = body()
    mutate(b)
    assert c.post(f"/internal/v1/exams/{t}/answer-key", json=b, headers=H).status_code == status
    assert keys(Session, t) == {1: "A", 2: "C"}


def test_unknown_test_is_404_and_a_partial_batch_is_all_or_nothing(env):
    c, Session, t = env
    assert c.post(f"/internal/v1/exams/{uuid.uuid4()}/answer-key", json=body(), headers=H).status_code == 404
    mixed = body(changes=[{"question_order": 1, "correct": "B"}, {"question_order": 77, "correct": "A"}])
    assert c.post(f"/internal/v1/exams/{t}/answer-key", json=mixed, headers=H).status_code == 404
    assert keys(Session, t) == {1: "A", 2: "C"}                       # question 1 was NOT changed
