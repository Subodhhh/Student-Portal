"""Run with: DATABASE_URL=sqlite:// REDIS_URL=redis://x pytest tests/test_internal_attempts.py (in-memory)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.test_attempt import AttemptStatus, TestAttempt as AttemptRow

H = {"X-Service-Key": "k-att"}


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AttemptRow.__table__.create(engine)
    from app.models.integrity import AttemptReview, IntegrityEvent
    IntegrityEvent.__table__.create(engine)
    AttemptReview.__table__.create(engine)
    from app.models.paper_selection import AttemptSelectedPaper
    AttemptSelectedPaper.__table__.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-att")
    with TestClient(app) as c:
        yield c, Session
    app.dependency_overrides.clear()


def add(Session, test_id, student, status, submitted=True):
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(AttemptRow(attempt_id=uuid.uuid4(), test_id=test_id, student_id=student, started_at=now, expires_at=now + timedelta(hours=3),
                          submitted_at=now if submitted else None, status=status))
        db.commit()


def test_key_required_and_disabled_by_default(env, monkeypatch):
    c, _ = env
    t = uuid.uuid4()
    assert c.get(f"/internal/v1/tests/{t}/attempts").status_code == 401
    assert c.get(f"/internal/v1/tests/{t}/attempts", headers={"X-Service-Key": "no"}).status_code == 401
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert c.get(f"/internal/v1/tests/{t}/attempts", headers=H).status_code == 404


def test_lists_only_this_tests_attempts_with_status_and_no_answers(env):
    c, Session = env
    a, b = uuid.uuid4(), uuid.uuid4()
    add(Session, a, "STU000001", AttemptStatus.SUBMITTED)
    add(Session, a, "STU000002", AttemptStatus.IN_PROGRESS, submitted=False)
    add(Session, a, "STU000003", AttemptStatus.AUTO_SUBMITTED)
    add(Session, b, "STU000009", AttemptStatus.SUBMITTED)
    rows = c.get(f"/internal/v1/tests/{a}/attempts", headers=H).json()
    assert sorted((r["student_id"], r["status"]) for r in rows) == [("STU000001", "SUBMITTED"), ("STU000002", "IN_PROGRESS"), ("STU000003", "AUTO_SUBMITTED")]
    assert set(rows[0]) == {"student_id", "attempt_id", "status", "started_at", "submitted_at", "expires_at",        # no answers / marks
                            "submit_reason", "review_status", "warnings", "retake_of_attempt_id",
                            "selected_papers"}
    assert c.get(f"/internal/v1/tests/{uuid.uuid4()}/attempts", headers=H).json() == []
