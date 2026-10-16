"""Run with: DATABASE_URL=sqlite:// pytest tests/test_internal_notifications.py (in-memory, never the real DB)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.notification import Notification, NotificationRecipient

KEY = "k-notif"
H = {"X-Service-Key": KEY}


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


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


def body(**over):
    b = {"dedupe_key": "exam-published:abc123", "event_type": "TEST_SCHEDULED", "title": "New test: Weekly Test",
         "message": "Starts 10 Jan 09:00", "metadata": {"test_id": "t1"}, "student_ids": ["STU000001", "STU000002"]}
    b.update(over)
    return b


def test_requires_service_key_and_disabled_by_default(env, monkeypatch):
    c, _ = env
    assert c.post("/internal/v1/notifications", json=body()).status_code == 401
    assert c.post("/internal/v1/notifications", json=body(), headers={"X-Service-Key": "x"}).status_code == 401
    c.cookies.set("session_id", "a-student-session")
    assert c.post("/internal/v1/notifications", json=body()).status_code == 401          # a student session is not a credential
    monkeypatch.setattr(settings, "institute_service_api_keys", "")
    assert c.post("/internal/v1/notifications", json=body(), headers=H).status_code == 404


def test_creates_one_inbox_entry_per_student(env):
    c, Session = env
    r = c.post("/internal/v1/notifications", json=body(), headers=H)
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["recipients"] == 2
    with Session() as db:
        n = db.scalars(select(Notification)).one()
        assert n.title == "New test: Weekly Test" and n.event_metadata["dedupe_key"] == "exam-published:abc123"
        recips = db.scalars(select(NotificationRecipient)).all()
        assert sorted((x.recipient_type.value, x.recipient_id, x.is_read) for x in recips) == [("STUDENT", "STU000001", False), ("STUDENT", "STU000002", False)]


def test_idempotent_per_dedupe_key(env):
    c, Session = env
    first = c.post("/internal/v1/notifications", json=body(), headers=H).json()
    again = c.post("/internal/v1/notifications", json=body(student_ids=["STU000009"]), headers=H)
    assert again.status_code == 200 and again.json()["created"] is False and again.json()["notification_id"] == first["notification_id"]
    with Session() as db:
        assert db.query(Notification).count() == 1 and db.query(NotificationRecipient).count() == 2   # replay never adds recipients
    other = c.post("/internal/v1/notifications", json=body(dedupe_key="result:xyz"), headers=H)
    assert other.status_code == 201


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(student_ids=[]),
    lambda b: b.update(student_ids=["bad id with spaces"]),
    lambda b: b.update(student_ids=[f"STU{i:06d}" for i in range(5001)]),
    lambda b: b.update(event_type="NOT_AN_EVENT"),
    lambda b: b.update(dedupe_key="short"),
    lambda b: b.update(title=""),
])
def test_invalid_input_rejected_and_nothing_written(env, mutate):
    c, Session = env
    b = body()
    mutate(b)
    assert c.post("/internal/v1/notifications", json=b, headers=H).status_code == 422
    with Session() as db:
        assert db.query(Notification).count() == 0


def test_students_still_read_it_through_the_existing_inbox_query(env):
    """The student-facing contract is untouched: the existing repository query returns the new entry."""
    from app.models.notification import RecipientType
    from app.services.notification_service import get_recipient_notifications, get_recipient_unread_count
    c, Session = env
    c.post("/internal/v1/notifications", json=body(), headers=H)
    with Session() as db:
        items = get_recipient_notifications(db=db, recipient_id="STU000001", recipient_type=RecipientType.STUDENT)
        assert [i["title"] for i in items] == ["New test: Weekly Test"]
        assert get_recipient_unread_count(db=db, recipient_id="STU000001", recipient_type=RecipientType.STUDENT) == 1
        assert get_recipient_unread_count(db=db, recipient_id="STU000003", recipient_type=RecipientType.STUDENT) == 0


# ---- the pre-existing, unauthenticated POST /notifications can be locked with an opt-in service key (contract unchanged when unset)
LEGACY = {"event_type": "SYSTEM_ANNOUNCEMENT", "title": "Hello", "message": "Body", "recipients": [{"recipient_type": "STUDENT", "recipient_id": "STU000001"}]}


def test_create_endpoint_disabled_when_no_key_is_configured(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(settings, "notification_create_api_keys", "")

    response = c.post("/notifications", json=LEGACY)

    assert response.status_code == 503


def test_legacy_create_endpoint_requires_the_service_key_once_configured(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(settings, "notification_create_api_keys", "old-key-123,new-key-456")
    assert c.post("/notifications", json=LEGACY).status_code == 401
    assert c.post("/notifications", json=LEGACY, headers={"X-Service-Key": "wrong"}).status_code == 401
    assert c.post("/notifications", json=LEGACY, headers={"X-Service-Key": "new-key-456"}).status_code == 201
    assert c.post("/notifications", json=LEGACY, headers={"X-Service-Key": "old-key-123"}).status_code == 201
