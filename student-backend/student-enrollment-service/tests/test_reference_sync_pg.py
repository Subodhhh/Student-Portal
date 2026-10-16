"""Read projection receiver (Institute -> Student), on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_reference_sync_pg.py

Needs the Student schema (providers/branches/batches/enrollments/enrollment_batches/students). Rows are uniquely identified per run."""
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

KEY = "k-sync"


@pytest.fixture()
def env(monkeypatch):
    from app.config import settings
    from app.main import app
    from app.routes import internal

    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[internal._db] = override
    monkeypatch.setattr(settings, "institute_service_api_keys", KEY)
    run = uuid.uuid4().hex[:8]
    sid = f"STU{run[:6].upper()}"[:20]
    with engine.begin() as c:
        c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                       "VALUES (:s, 'Ref', 'Sync', :e, 'ACTIVE', now(), now())"), {"s": sid, "e": f"ref-{run}@example.com"})
    with TestClient(app) as client:
        yield client, engine, run, sid
    app.dependency_overrides.clear()


def ids():
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def payload(run, sid, p, b, bt, eb, enrollment_id, batches=True, status="ACTIVE"):
    return {
        "providers": [{"provider_id": p, "name": f"P {run}", "email": f"P-{run}@Example.com", "status": "ACTIVE"}],
        "branches": [{"branch_id": b, "provider_id": p, "name": f"B {run}", "city": "Pune", "status": "ACTIVE"}],
        "batches": [{"batch_id": bt, "branch_id": b, "name": f"Batch {run}", "status": "ACTIVE"}],
        "enrollments": [{"enrollment_id": enrollment_id, "provider_id": p, "branch_id": b, "student_id": sid, "status": status,
                         "enrolled_at": "2026-10-16T10:00:00+05:30",
                         "batches": [{"enrollment_batch_id": eb, "batch_id": bt}] if batches else []}],
    }


def test_requires_service_key(env):
    client, *_ = env
    assert client.post("/internal/v1/reference/sync", json={}).status_code == 401
    assert client.post("/internal/v1/reference/sync", json={}, headers={"X-Service-Key": "nope"}).status_code == 401


def test_upsert_is_idempotent_and_membership_is_replaced_as_a_set(env):
    client, engine, run, sid = env
    p, b, bt = ids()
    eb, enr = str(uuid.uuid4()), str(uuid.uuid4())
    H = {"X-Service-Key": KEY}
    for _ in range(2):   # replay = same state
        r = client.post("/internal/v1/reference/sync", json=payload(run, sid, p, b, bt, eb, enr), headers=H)
        assert r.status_code == 200 and r.json()["errors"] == [], r.text
    with engine.connect() as c:
        assert c.execute(text("SELECT email FROM providers WHERE provider_id = :p"), {"p": p}).scalar() == f"p-{run}@example.com"
        assert c.execute(text("SELECT count(*) FROM enrollment_batches WHERE enrollment_id = :e"), {"e": enr}).scalar() == 1
        # enrolled_at is stored as naive UTC
        assert str(c.execute(text("SELECT enrolled_at FROM enrollments WHERE enrollment_id = :e"), {"e": enr}).scalar()) == "2026-10-16 04:30:00"
    r = client.post("/internal/v1/reference/sync", json=payload(run, sid, p, b, bt, eb, enr, batches=False, status="INACTIVE"), headers=H)
    assert r.json()["errors"] == []
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM enrollment_batches WHERE enrollment_id = :e"), {"e": enr}).scalar() == 0
        assert c.execute(text("SELECT status::text FROM enrollments WHERE enrollment_id = :e"), {"e": enr}).scalar() == "INACTIVE"


def test_a_bad_row_is_reported_and_never_blocks_the_others(env):
    client, engine, run, sid = env
    p, b, bt = ids()
    body = payload(run, sid, p, b, bt, str(uuid.uuid4()), str(uuid.uuid4()))
    unknown = dict(body["enrollments"][0], enrollment_id=str(uuid.uuid4()), student_id="STU_UNKNOWN", batches=[])
    body["enrollments"].append(unknown)
    r = client.post("/internal/v1/reference/sync", json=body, headers={"X-Service-Key": KEY})
    out = r.json()
    assert r.status_code == 200
    assert [e["id"] for e in out["errors"]] == [unknown["enrollment_id"]] and out["errors"][0]["reason"] == "constraint"
    assert len(out["applied"]["enrollments"]) == 1 and out["applied"]["providers"] == [p]
    assert "STU_UNKNOWN" not in r.text   # no row values echoed back
