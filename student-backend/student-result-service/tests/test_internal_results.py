"""Run with: DATABASE_URL=sqlite:// pytest tests/test_internal_results.py (in-memory, never the real DB)."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.test_result import TestResult as ResultRow

H = {"X-Service-Key": "k-res"}


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
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-res")
    with TestClient(app) as c:
        yield c, Session
    app.dependency_overrides.clear()


def seed(Session, test_id, rows):
    with Session() as db:
        for sid, obt in rows:
            db.add(ResultRow(attempt_id=uuid.uuid4(), test_id=test_id, student_id=sid, total_questions=3, attempted_questions=3,
                             correct_answers=2, incorrect_answers=1, unanswered_questions=0, total_marks=12, obtained_marks=obt, accuracy=66.7))
        db.commit()


def test_key_required_and_disabled_by_default(env, monkeypatch):
    c, _ = env
    t = uuid.uuid4()
    assert c.get(f"/internal/v1/tests/{t}/results").status_code == 401
    assert c.get(f"/internal/v1/tests/{t}/results", headers={"X-Service-Key": "x"}).status_code == 401
    c.cookies.set("session_id", "student")
    assert c.get(f"/internal/v1/tests/{t}/results").status_code == 401
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert c.get(f"/internal/v1/tests/{t}/results", headers=H).status_code == 404


def test_returns_only_requested_exam_ordered_by_marks(env):
    c, Session = env
    a, b = uuid.uuid4(), uuid.uuid4()
    seed(Session, a, [("STU000001", 7), ("STU000002", 10)])
    seed(Session, b, [("STU000009", 12)])
    rows = c.get(f"/internal/v1/tests/{a}/results", headers=H).json()
    assert [(r["student_id"], r["obtained_marks"]) for r in rows] == [("STU000002", 10), ("STU000001", 7)]
    assert c.get(f"/internal/v1/tests/{uuid.uuid4()}/results", headers=H).json() == []
