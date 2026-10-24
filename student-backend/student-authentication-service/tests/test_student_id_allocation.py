"""Student IDs come from the durable counter `student_id_allocator`, never from the highest existing student: an ID that was ever issued is
never issued to another student, even after students (or all of them) are deleted or the service restarts.
In-memory / file SQLite and fake Redis; concurrency and the migration are covered on PostgreSQL in test_student_id_allocation_pg.py.
Run with dummy env, e.g.:  DATABASE_URL=sqlite:// REDIS_URL=redis://x CORS_ORIGINS=x ..."""
import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.main import app
from app.models.student import Student
from app.models.student_id_allocator import StudentIdAllocator
from app.routes import internal
from app.services import student_ids

KEY = "test-internal-key"
H = {"X-Service-Key": KEY}


@compiles(INET, "sqlite")
def _inet_sqlite(type_, compiler, **kw):
    return "VARCHAR(45)"


def _database(url="sqlite://", seed: int | None = 0):
    kw = {"connect_args": {"check_same_thread": False}}
    if url == "sqlite://":
        kw["poolclass"] = StaticPool
    engine = create_engine(url, **kw)
    Student.__table__.create(engine, checkfirst=True)
    StudentIdAllocator.__table__.create(engine, checkfirst=True)
    session = sessionmaker(bind=engine, autoflush=False)
    if seed is not None:
        with session() as s:
            if s.get(StudentIdAllocator, "STU") is None:
                s.add(StudentIdAllocator(name="STU", last_value=seed))
                s.commit()
    return engine, session


@pytest.fixture()
def api(monkeypatch):
    """A client factory: api(seed=0) -> (client, Session) on a fresh database whose counter starts at `seed` (None: no counter row)."""
    monkeypatch.setattr(internal, "redis_client", fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr(settings, "institute_service_api_keys", KEY)
    opened = []

    def make(seed=0, url="sqlite://"):
        _engine, session = _database(url, seed)
        monkeypatch.setattr(internal, "SessionLocal", session)
        c = TestClient(app)
        c.__enter__()
        opened.append(c)
        return c, session
    yield make
    for c in opened:
        c.__exit__(None, None, None)


def create(c, n, key=None):
    r = c.post("/internal/v1/students", json={"first_name": "S", "last_name": str(n), "email": f"s{n}@example.com"},
               headers={**H, "Idempotency-Key": key or f"k-{n}"})
    assert r.status_code == 201, r.text
    return r.json()["student_id"]


def counter(session) -> int:
    with session() as s:
        return s.get(StudentIdAllocator, "STU").last_value


def drop(session, *ids):
    with session() as s:
        s.execute(delete(Student).where(Student.student_id.in_(ids)) if ids else delete(Student))
        s.commit()


def test_first_and_sequential_ids_keep_the_format(api):
    c, _ = api(seed=0)
    assert [create(c, i) for i in range(3)] == ["STU000001", "STU000002", "STU000003"]
    c, session = api(seed=100)                                 # the floor the migration seeds (safety margin)
    assert create(c, 1) == "STU000101" and counter(session) == 101


def test_deleting_the_highest_student_never_frees_its_id(api):
    c, session = api(seed=0)
    first, second = create(c, 1), create(c, 2)
    drop(session, second)                                     # the highest-numbered student is deleted
    assert create(c, 3) == "STU000003"                        # MAX+1 would have re-issued STU000002
    assert {first, second}.isdisjoint({create(c, 4)})


def test_deleting_every_student_continues_after_the_highest_ever_issued(api):
    c, session = api(seed=100)
    issued = [create(c, i) for i in range(3)]                  # STU000101..103
    drop(session)                                             # the planned student-data reset deletes every student
    with session() as s:
        assert s.scalar(select(Student.student_id)) is None and counter(session) == 103
    after = [create(c, i + 10) for i in range(2)]
    assert after == ["STU000104", "STU000105"] and set(after).isdisjoint(issued)


def test_ids_never_reappear_across_create_and_delete_cycles(api):
    c, session = api(seed=0)
    seen = []
    for i in range(20):                                       # every student is deleted right after creation
        sid = create(c, i)
        drop(session, sid)
        seen.append(sid)
    assert len(set(seen)) == 20                               # so a retained reference can never point at a different person


def test_a_repeated_request_returns_the_same_student_without_issuing_another_id(api):
    c, session = api(seed=0)
    a, b = create(c, 1, key="same"), create(c, 1, key="same")
    assert a == b == "STU000001" and counter(session) == 1


def test_a_failed_creation_issues_nothing_and_a_retry_works(api):
    c, session = api(seed=0)
    create(c, 1)
    r = c.post("/internal/v1/students", json={"first_name": "S", "last_name": "x", "email": "s1@example.com"}, headers={**H, "Idempotency-Key": "dup"})
    assert r.status_code == 409 and counter(session) == 1     # duplicate email: nothing allocated
    with session() as s:                                      # an allocation whose transaction rolls back was never issued
        assert student_ids.allocate(s) == "STU000002"
        s.rollback()
    assert counter(session) == 1
    assert create(c, 2) == "STU000002"                        # the retry gets it (nobody ever held it)


def test_an_id_taken_outside_the_allocator_is_skipped_forever(api):
    c, session = api(seed=0)
    with session() as s:                                      # a row inserted by hand, bypassing the counter
        s.add(Student(student_id="STU000001", first_name="Hand", last_name="Made", email="hand@example.com", status="ACTIVE"))
        s.commit()
    assert create(c, 1) == "STU000002" and counter(session) == 2


def test_the_counter_survives_a_restart(api, tmp_path):
    url = f"sqlite:///{tmp_path / 'auth.db'}"
    c, _ = api(seed=0, url=url)
    assert create(c, 1) == "STU000001"
    c2, _ = api(seed=0, url=url)                               # a new engine and sessions: as after a container restart
    assert create(c2, 2) == "STU000002"


def test_without_the_counter_row_creation_refuses_instead_of_guessing(api):
    c, session = api(seed=None)
    r = c.post("/internal/v1/students", json={"first_name": "S", "last_name": "x", "email": "x@example.com"}, headers={**H, "Idempotency-Key": "k"})
    assert r.status_code == 503
    with session() as s:
        assert s.scalar(select(Student.student_id)) is None
