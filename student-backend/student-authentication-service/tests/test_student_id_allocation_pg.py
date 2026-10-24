"""Student ID allocator on a real but DISPOSABLE PostgreSQL: concurrent creations never share an ID, rolled-back allocations are never
issued, the counter survives a restart, and migration 0002_student_id_allocator seeds it from the historical floor and from every
student_id present without changing existing students - also through a connection whose session search_path is empty (a contaminated
Neon pooled connection). Each test creates and drops its own database `auth_verify_<random>`.
Skipped unless STUDENT_AUTH_TEST_PG points at a LOCAL server, e.g. postgresql+psycopg://postgres@127.0.0.1:55433/postgres"""
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

BASE = os.environ.get("STUDENT_AUTH_TEST_PG", "")
pytestmark = pytest.mark.skipif(not re.match(r"^postgresql(\+\w+)?://[^@/]*@(127\.0\.0\.1|localhost)(:\d+)?/", BASE),
                                reason="needs a disposable local PostgreSQL (STUDENT_AUTH_TEST_PG)")
SERVICE = Path(__file__).resolve().parents[1]


@pytest.fixture()
def db_url():
    name = f"auth_verify_{uuid.uuid4().hex[:8]}"
    admin = create_engine(make_url(BASE), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'create database "{name}"'))
    url = make_url(BASE).set(database=name)
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'drop database if exists "{name}" with (force)'))
        admin.dispose()


def _tables(url, seed=0):
    from app.models.password_reset_token import PasswordSetupResetToken
    from app.models.student import Student
    from app.models.student_id_allocator import StudentIdAllocator
    engine = create_engine(url)
    for model in (Student, PasswordSetupResetToken, StudentIdAllocator):
        model.__table__.create(engine, checkfirst=True)
    if seed is not None:
        with engine.begin() as c:
            c.execute(text("insert into student_id_allocator (name, last_value) values ('STU', :v)"), {"v": seed})
    return engine


def _create(session_factory, n, rollback=False):
    from app.models.student import Student
    from app.services import student_ids
    with session_factory() as s:
        sid = student_ids.allocate(s)
        s.add(Student(student_id=sid, first_name="C", last_name=str(n), email=f"c{n}-{uuid.uuid4().hex[:6]}@example.com", status="ACTIVE"))
        if rollback:
            s.rollback()
            return None
        s.commit()
        return sid


def test_concurrent_creations_never_share_an_id(db_url):
    engine = _tables(db_url)
    factory = sessionmaker(bind=engine, autoflush=False)
    start, out, errors = threading.Barrier(20), [], []

    def worker(i):
        try:
            start.wait()
            out.append(_create(factory, i))
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(repr(exc))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(set(out)) == 20 and sorted(out) == [f"STU{n:06d}" for n in range(1, 21)]
    with engine.connect() as c:
        assert c.execute(text("select last_value from student_id_allocator")).scalar() == 20


def test_concurrent_rollbacks_never_issue_an_id_twice(db_url):
    engine = _tables(db_url, seed=12)
    factory = sessionmaker(bind=engine, autoflush=False)
    start, out = threading.Barrier(16), []

    def worker(i):
        start.wait()
        out.append(_create(factory, i, rollback=(i % 2 == 0)))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    committed = [s for s in out if s]
    assert len(committed) == 8 and len(set(committed)) == 8 and min(committed) > "STU000012"
    with engine.connect() as c:
        rows = [r for (r,) in c.execute(text("select student_id from students order by 1"))]
        assert rows == sorted(committed) and c.execute(text("select last_value from student_id_allocator")).scalar() == 20


def test_the_counter_survives_a_restart_and_deletions(db_url):
    engine = _tables(db_url)
    first = _create(sessionmaker(bind=engine, autoflush=False), 1)
    engine.dispose()                                                     # the service stops
    engine2 = create_engine(db_url)                                      # and starts again
    with engine2.begin() as c:
        c.execute(text("delete from students"))                          # every student deleted in between
    assert _create(sessionmaker(bind=engine2, autoflush=False), 2) == "STU000002" != first


def _alembic(url, *args):
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False), "REDIS_URL": "redis://x", "CORS_ORIGINS": "http://x",
           "SESSION_COOKIE_SECURE": "false", "FRONTEND_RESET_URL": "http://x/reset-password", "PYTHONPATH": "."}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=SERVICE, env=env, capture_output=True, text=True, check=False)


def _live_like(url, ids, other_ids=()):
    """A database like the live Student database: students + tokens at revision 562cc1f6c4b7, plus another table with student_id."""
    engine = _tables(url, seed=None)
    with engine.begin() as c:
        for sid in ids:
            c.execute(text("insert into students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                           "values (:s, 'L', 'L', :e, 'ACTIVE', now(), now())"),
                      {"s": sid, "e": f"{sid.lower()}@example.com"})
        c.execute(text("create table enrollments (enrollment_id varchar(36) primary key, student_id varchar(20))"))
        for i, sid in enumerate(other_ids):
            c.execute(text("insert into enrollments values (:e, :s)"), {"e": f"e{i}", "s": sid})
        c.execute(text("create table alembic_version (version_num varchar(32) primary key)"))
        c.execute(text("insert into alembic_version values ('562cc1f6c4b7')"))
        c.execute(text(f"""alter database "{url.database}" set search_path to ''"""))   # every session starts like a contaminated pooled one
    engine.dispose()


def _state(url):
    engine = create_engine(url)
    with engine.connect() as c:
        out = {"head": c.execute(text("select version_num from public.alembic_version")).scalar(),
               "allocator": c.execute(text("select to_regclass('public.student_id_allocator')::text")).scalar(),
               "students": c.execute(text("select string_agg(student_id || ':' || email, ',' order by student_id) from public.students")).scalar(),
               "search_path": c.execute(text("show search_path")).scalar()}
        if out["allocator"]:
            out["counter"] = c.execute(text("select last_value from public.student_id_allocator where name = 'STU'")).scalar()
    engine.dispose()
    return out


def test_migration_seeds_from_the_historical_floor_and_keeps_students(db_url):
    _live_like(db_url, ["STU000001", "STU000002"])
    before = _state(db_url)
    up = _alembic(db_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    after = _state(db_url)
    assert after["head"] == "0002_student_id_allocator" and after["counter"] == 100          # the safety floor (evidence: STU000012)
    assert after["students"] == before["students"] and after["search_path"] == '""'           # students untouched; nothing leaked
    again = _alembic(db_url, "upgrade", "head")
    assert again.returncode == 0 and _state(db_url)["counter"] == 100                          # re-running never reseeds
    down = _alembic(db_url, "downgrade", "562cc1f6c4b7")                                       # disposable database only
    assert down.returncode == 0, down.stderr[-2000:]
    assert _state(db_url)["allocator"] is None and _state(db_url)["students"] == before["students"]
    re_up = _alembic(db_url, "upgrade", "head")
    assert re_up.returncode == 0 and _state(db_url)["counter"] == 100
    engine = create_engine(db_url)
    with engine.begin() as c:                                                                  # IDs issued since: counter now 250
        c.execute(text("update public.student_id_allocator set last_value = 250"))
    engine.dispose()
    assert _alembic(db_url, "upgrade", "head").returncode == 0 and _state(db_url)["counter"] == 250   # a later run never lowers it


def test_migration_seeds_above_any_student_id_present_anywhere(db_url):
    _live_like(db_url, ["STU000003"], other_ids=["STU000150"])     # a reference in another table is above the floor and any student row
    assert _alembic(db_url, "upgrade", "head").returncode == 0
    assert _state(db_url)["counter"] == 150
