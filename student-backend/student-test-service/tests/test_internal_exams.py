"""Run with dummy env and an in-memory DB (never the configured one):
   DATABASE_URL=sqlite:// pytest tests/test_internal_exams.py"""
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
from app.models import test_audience as audience_models

KEY = "k-test"
H = {"X-Service-Key": KEY}


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
    monkeypatch.setattr(settings, "institute_service_api_keys", KEY)
    with TestClient(app) as c:
        yield c, Session
    app.dependency_overrides.clear()


def payload(**over):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    body = {
        "operation": "PUBLISH", "exam_version_id": str(uuid.uuid4()), "provider_id": str(uuid.uuid4()), "branch_id": str(uuid.uuid4()),
        "name": "Weekly Test", "description": None, "duration_minutes": 180,
        "start_at": start.isoformat(), "end_at": (start + timedelta(hours=6)).isoformat(),
        "student_ids": ["STU000001", "STU000002", "STU000001"],
        "questions": [
            {"order": 1, "subject": "Physics", "text": "Q1", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "correct": "A", "marks": 4, "negative_marks": 1},
            {"order": 2, "subject": "Chemistry", "text": "Q2", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "correct": "C", "marks": 4, "negative_marks": 1},
        ],
    }
    body.update(over)
    return body


def test_requires_service_key_and_is_disabled_by_default(env, monkeypatch):
    c, _ = env
    assert c.post("/internal/v1/exams", json=payload()).status_code == 401
    assert c.post("/internal/v1/exams", json=payload(), headers={"X-Service-Key": "bad"}).status_code == 401
    c.cookies.set("session_id", "student-session")
    assert c.post("/internal/v1/exams", json=payload()).status_code == 401       # a student session is not a credential
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert c.post("/internal/v1/exams", json=payload(), headers=H).status_code == 404


def test_publish_creates_test_questions_and_student_audiences(env):
    c, Session = env
    body = payload()
    r = c.post("/internal/v1/exams", json=body, headers=H)
    assert r.status_code == 201 and r.json() == {"status": "published", "questions": 2, "students": 2}
    with Session() as db:
        t = db.get(test_models.Test, uuid.UUID(body["exam_version_id"]))
        assert t.status == "ACTIVE" and t.total_questions == 2 and t.total_marks == 8 and t.duration_minutes == 180
        qs = db.scalars(select(question_models.Question).where(question_models.Question.test_id == t.test_id).order_by(question_models.Question.question_order)).all()
        assert [(q.subject, q.correct_answer, q.marks, q.negative_marks) for q in qs] == [("Physics", "A", 4, 1), ("Chemistry", "C", 4, 1)]
        auds = db.scalars(select(audience_models.TestAudience).where(audience_models.TestAudience.test_id == t.test_id)).all()
        assert sorted(a.student_id for a in auds) == ["STU000001", "STU000002"]
        assert all(a.audience_type == audience_models.AudienceType.STUDENT for a in auds)


def test_replay_is_idempotent_and_never_rewrites(env):
    c, Session = env
    body = payload()
    assert c.post("/internal/v1/exams", json=body, headers=H).status_code == 201
    changed = {**body, "name": "Tampered", "student_ids": ["STU999999"]}
    again = c.post("/internal/v1/exams", json=changed, headers=H)
    assert again.status_code == 201 and again.json() == {"status": "already_published"}
    with Session() as db:
        assert db.query(test_models.Test).count() == 1
        assert db.get(test_models.Test, uuid.UUID(body["exam_version_id"])).name == "Weekly Test"
        assert db.query(audience_models.TestAudience).count() == 2


def test_cancel_deactivates_without_deleting(env):
    c, Session = env
    body = payload()
    c.post("/internal/v1/exams", json=body, headers=H)
    r = c.post("/internal/v1/exams", json={**body, "operation": "CANCEL"}, headers=H)
    assert r.json() == {"status": "cancelled"}
    with Session() as db:
        assert db.get(test_models.Test, uuid.UUID(body["exam_version_id"])).status == "INACTIVE"
        assert db.query(question_models.Question).count() == 2
    assert c.post("/internal/v1/exams", json={**payload(), "operation": "CANCEL"}, headers=H).json() == {"status": "not_found_noop"}


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(questions=[]),
    lambda b: b["questions"][1].update(order=5),
    lambda b: b["questions"][0].update(options={"A": "a", "B": "b"}),
    lambda b: b["questions"][0].update(correct="Z"),
    lambda b: b.update(end_at=b["start_at"]),
    lambda b: b.update(start_at="2030-01-01T10:00:00"),          # no UTC offset
    lambda b: b.update(duration_minutes=0),
])
def test_invalid_payloads_rejected_and_nothing_written(env, mutate):
    c, Session = env
    body = payload()
    mutate(body)
    assert c.post("/internal/v1/exams", json=body, headers=H).status_code == 422
    with Session() as db:
        assert db.query(test_models.Test).count() == 0 and db.query(question_models.Question).count() == 0


def test_times_are_stored_in_the_session_zone(env):
    """On PostgreSQL the database converts to its own session time zone (verified separately against a real
    server); on the SQLite used here the documented fallback is naive UTC."""
    c, Session = env
    body = payload(start_at="2030-01-01T05:30:00+05:30", end_at="2030-01-01T11:30:00+05:30")
    c.post("/internal/v1/exams", json=body, headers=H)
    with Session() as db:
        t = db.get(test_models.Test, uuid.UUID(body["exam_version_id"]))
        assert t.start_at == datetime(2030, 1, 1, 0, 0) and t.end_at == datetime(2030, 1, 1, 6, 0)


def test_a_unified_global_test_is_stored_as_its_date_and_listed_as_the_global_test(env):
    """One exam per student per Global Test date (papers from several providers): the card shows the Global Test, not the anchor provider."""
    from app.services.test_service import format_test
    c, Session = env
    occurrence = str(uuid.uuid4())
    unified, ordinary = payload(global_occurrence_id=occurrence, student_ids=["STU000001"]), payload()
    assert c.post("/internal/v1/exams", json=unified, headers=H).status_code == 201
    assert c.post("/internal/v1/exams", json=ordinary, headers=H).status_code == 201
    with Session() as db:
        u = db.get(test_models.Test, uuid.UUID(unified["exam_version_id"]))
        o = db.get(test_models.Test, uuid.UUID(ordinary["exam_version_id"]))
        assert str(u.global_occurrence_id) == occurrence and o.global_occurrence_id is None     # older/ordinary tests unchanged
        row = {"test_id": u.test_id, "provider_id": u.provider_id, "provider_name": "P", "branch_id": u.branch_id, "branch_name": "B", "city": None,
               "state": None, "name": u.name, "description": None, "duration_minutes": u.duration_minutes, "total_questions": u.total_questions,
               "total_marks": u.total_marks, "start_at": u.start_at, "end_at": u.end_at, "status": u.status, "global_occurrence_id": u.global_occurrence_id}
        assert str(format_test(row, db)["global_occurrence_id"]) == occurrence
        from app.schemas.test import TestResponse                          # what the student listing endpoints serialise through
        assert str(TestResponse.model_validate(format_test(row, db)).global_occurrence_id) == occurrence
