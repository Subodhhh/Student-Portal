"""Paper migration 0006_upload_recognition on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server).

  * a NEW database (Student-chain tables only, as tests/e2e/run-stack.sh builds one): every Institute chain to head - the paper baseline already creates paper_uploads.recognition from the
    models, and 0006 must adopt it instead of failing (the defect the disposable E2E found);
  * a database migrated BEFORE the column joined the models (paper_uploads without it at 0005): 0006 adds it - nullable JSONB, no
    default, existing rows NULL;
  * an existing column of exactly the right shape: adopted unchanged; of any other shape (type, NOT NULL, default): the upgrade stops
    and the database stays at 0005;
  * downgrade and upgrade again.
Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
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
BACKEND = Path(__file__).resolve().parents[2]
# the Student-chain tables the Institute chains reference (providers, ...), as run-stack.sh creates them in a new database
STUDENT_SCHEMA = BACKEND.parent / "tests" / "e2e" / "student_chain_schema.sql"
COMMON = BACKEND / "institute-common"
BEFORE_PAPER = ("identity", "organization", "academics", "enrollment")          # the order the deployments and the E2E harness use
AFTER_PAPER = ("exam", "operations")
PAPER_HEAD = "0017_taxonomy_aliases"                                                                 # the latest paper revision


@pytest.fixture()
def db():
    base = make_url(os.environ["TEST_DATABASE_URL"])
    name = f"inst_verify_paper0006_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'create database "{name}"'))
    url = base.set(database=name)
    engine = create_engine(url)
    with engine.begin() as c:
        c.exec_driver_sql(STUDENT_SCHEMA.read_text())
    engine.dispose()
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'drop database if exists "{name}" with (force)'))
        admin.dispose()


def alembic(url, service, *args):
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False), "CORS_ORIGINS": "http://x",
           "PYTHONPATH": f".{os.pathsep}{COMMON}"}
    env.pop("MIGRATION_DATABASE_URL", None)                                    # never another database than this one
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND / f"institute-{service}-service", env=env,
                          capture_output=True, text=True, check=False)


def run(url, service, *args):
    r = alembic(url, service, *args)
    assert r.returncode == 0, f"{service} {args}: {r.stderr[-2000:]}"


def paper_at(url, revision):
    for s in BEFORE_PAPER:
        run(url, s, "upgrade", "head")
    run(url, "paper", "upgrade", revision)


def sql(url, q):
    """Run one statement; its rows if it returns any."""
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            result = c.execute(text(q))
            return result.all() if result.returns_rows else []
    finally:
        engine.dispose()


def column(url):
    rows = sql(url, "select data_type, is_nullable, column_default, ordinal_position from information_schema.columns "
                    "where table_schema = 'public' and table_name = 'paper_uploads' and column_name = 'recognition'")
    return rows[0] if rows else None


def head(url):
    return sql(url, "select version_num from inst_paper_alembic_version")[0][0]


def test_a_new_database_migrates_every_chain_to_head(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    assert head(db) == PAPER_HEAD                             # the paper chain head (0007 and 0008 follow 0006)
    assert column(db)[:3] == ("jsonb", "YES", None)


def test_the_baseline_column_is_adopted_unchanged(db):
    paper_at(db, "0005_paper_list_indexes")
    before = column(db)
    assert before is not None and before[:3] == ("jsonb", "YES", None)          # created by 0001 from the current models
    run(db, "paper", "upgrade", "0006_upload_recognition")
    assert head(db) == "0006_upload_recognition" and column(db) == before      # same column, same position: not re-created


def test_a_database_from_before_the_column_gets_it(db):
    paper_at(db, "0005_paper_list_indexes")
    sql(db, "alter table paper_uploads drop column recognition")                # the shape of a database migrated before 2.3
    assert column(db) is None
    run(db, "paper", "upgrade", "0006_upload_recognition")
    assert head(db) == "0006_upload_recognition" and column(db)[:3] == ("jsonb", "YES", None)


@pytest.mark.parametrize("change, message", [
    ("alter column recognition type text", "type TEXT (expected JSONB)"),
    ("alter column recognition set not null", "NOT NULL"),
    ("alter column recognition set default '{}'::jsonb", "default"),
])
def test_an_existing_column_of_another_shape_stops_the_upgrade(db, change, message):
    paper_at(db, "0005_paper_list_indexes")
    sql(db, f"alter table paper_uploads {change}")
    r = alembic(db, "paper", "upgrade", "head")
    assert r.returncode != 0 and message in r.stderr, r.stderr[-1500:]
    assert head(db) == "0005_paper_list_indexes"                               # nothing recorded as applied


def test_downgrade_and_upgrade_again(db):
    paper_at(db, "head")
    run(db, "paper", "downgrade", "0005_paper_list_indexes")
    assert column(db) is None and head(db) == "0005_paper_list_indexes"
    run(db, "paper", "upgrade", "head")
    assert column(db)[:3] == ("jsonb", "YES", None) and head(db) == PAPER_HEAD
