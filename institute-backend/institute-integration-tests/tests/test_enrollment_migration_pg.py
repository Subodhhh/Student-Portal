"""Enrollment migrations 0003 + 0004 and academics 0002 through a connection whose session search_path is EMPTY - what a Neon pooled connection carries after
another client (pg_dump) emptied it. Upgrade and downgrade must still work, create/drop only the new tables (enrollment_batch_subjects,
enrollment_academic_profiles, batch_academic_defaults), leave existing rows alone, and never leak the setting into later sessions. Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.skipif(not (os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"),
                                reason="needs a disposable PostgreSQL (TEST_DATABASE_URL)")
SERVICE = Path(__file__).resolve().parents[2] / "institute-enrollment-service"
ACADEMICS = Path(__file__).resolve().parents[2] / "institute-academics-service"
COMMON = Path(__file__).resolve().parents[2] / "institute-common"


@pytest.fixture()
def empty_path_db():
    """A throwaway database at enrollment head 0002 (only the tables 0003 references), whose sessions all start with search_path ''."""
    base = make_url(os.environ["TEST_DATABASE_URL"])
    name = f"inst_verify_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'create database "{name}"'))
    url = base.set(database=name)
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("create table public.subjects (subject_id uuid primary key, name varchar(120) not null)"))
        c.execute(text("create table public.enrollment_batches (enrollment_batch_id uuid primary key, batch_id uuid not null)"))
        c.execute(text("create table public.enrollments (enrollment_id varchar(36) primary key)"))
        c.execute(text("create table public.batches (batch_id uuid primary key)"))
        c.execute(text("create table public.inst_enrollment_alembic_version (version_num varchar(32) primary key)"))
        c.execute(text("insert into public.inst_enrollment_alembic_version values ('0002_enrollment_student_identity')"))
        c.execute(text("create table public.inst_academics_alembic_version (version_num varchar(32) primary key)"))
        c.execute(text("insert into public.inst_academics_alembic_version values ('0001_academics_baseline')"))
        c.execute(text("insert into public.enrollments values ('e1')"))
        c.execute(text("insert into public.batches values (gen_random_uuid())"))
        c.execute(text("insert into public.subjects values (gen_random_uuid(), 'Physics')"))
        c.execute(text("insert into public.enrollment_batches values (gen_random_uuid(), gen_random_uuid())"))
        c.execute(text(f"""alter database "{name}" set search_path to ''"""))
    engine.dispose()
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'drop database if exists "{name}" with (force)'))
        admin.dispose()


def alembic(url, *args, service=SERVICE):
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False), "CORS_ORIGINS": "http://x",
           "PYTHONPATH": f".{os.pathsep}{COMMON}"}
    env.pop("MIGRATION_DATABASE_URL", None)
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=service, env=env, capture_output=True, text=True, check=False)


def state(url):
    engine = create_engine(url)
    with engine.connect() as c:
        out = {"search_path": c.execute(text("show search_path")).scalar(),
               "head": c.execute(text("select version_num from public.inst_enrollment_alembic_version")).scalar(),
               "table": c.execute(text("select to_regclass('public.enrollment_batch_subjects')::text")).scalar(),
               "profiles": c.execute(text("select to_regclass('public.enrollment_academic_profiles')::text")).scalar(),
               "defaults": c.execute(text("select to_regclass('public.batch_academic_defaults')::text")).scalar(),
               "academics_head": c.execute(text("select version_num from public.inst_academics_alembic_version")).scalar(),
               "rows": c.execute(text("select (select count(*) from public.subjects), (select count(*) from public.enrollment_batches), "
                                      "(select count(*) from public.enrollments), (select count(*) from public.batches)")).one()}
    engine.dispose()
    return out


def test_upgrade_and_downgrade_work_with_an_empty_session_search_path(empty_path_db):
    before = state(empty_path_db)
    assert before["search_path"] == '""' and before["table"] is None          # the contaminated starting point
    up = alembic(empty_path_db, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    after = state(empty_path_db)
    assert after["head"] == "0004_enrollment_academics" and after["table"] == "public.enrollment_batch_subjects"
    assert after["profiles"] == "public.enrollment_academic_profiles"
    assert after["search_path"] == '""'                                       # SET LOCAL never leaked into later sessions
    assert after["rows"] == before["rows"]                                    # existing rows untouched
    down = alembic(empty_path_db, "downgrade", "0002_enrollment_student_identity")
    assert down.returncode == 0, down.stderr[-2000:]
    final = state(empty_path_db)
    assert final["head"] == "0002_enrollment_student_identity" and final["table"] is None and final["profiles"] is None
    assert final["rows"] == before["rows"]                                    # the downgrade drops only the new tables
    again = alembic(empty_path_db, "upgrade", "head")                         # and the chain re-applies cleanly
    assert again.returncode == 0, again.stderr[-2000:]
    assert state(empty_path_db)["profiles"] == "public.enrollment_academic_profiles"


def test_academics_batch_defaults_migration_with_an_empty_session_search_path(empty_path_db):
    before = state(empty_path_db)
    up = alembic(empty_path_db, "upgrade", "head", service=ACADEMICS)
    assert up.returncode == 0, up.stderr[-2000:]
    after = state(empty_path_db)
    assert after["academics_head"] == "0002_batch_academic_defaults" and after["defaults"] == "public.batch_academic_defaults"
    assert after["search_path"] == '""' and after["rows"] == before["rows"]
    down = alembic(empty_path_db, "downgrade", "0001_academics_baseline", service=ACADEMICS)
    assert down.returncode == 0, down.stderr[-2000:]
    final = state(empty_path_db)
    assert final["academics_head"] == "0001_academics_baseline" and final["defaults"] is None and final["rows"] == before["rows"]
