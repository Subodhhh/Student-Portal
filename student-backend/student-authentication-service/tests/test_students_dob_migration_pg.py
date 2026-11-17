"""Migration 0003_students_date_of_birth on a real but DISPOSABLE PostgreSQL (each test creates and drops its own database):

  * a NEW, EMPTY database reaches head through the whole chain (the roots create VARCHAR(20) Student IDs), with the same schema as an
    existing database, and the model matches (alembic check never proposes dropping date_of_birth);
  * a NEW database built in the live shape (VARCHAR(20) IDs, as the E2E harness builds it) gets students.date_of_birth (DATE, nullable,
    no default);
  * an EXISTING live-like database (students + tokens at 562cc1f6c4b7, as the other PostgreSQL tests build it) with students: upgraded to
    head through 0002 and 0003 - the column is added, every student row is unchanged and its date_of_birth is NULL;
  * a database that already has the column (the legacy schema had it) with values: adopted unchanged, values kept;
  * an existing column of another shape (type, NOT NULL, default): the upgrade stops at 0002 and nothing is recorded as applied;
  * downgrade to 0002 removes the column; upgrading again re-adds it.
Skipped unless STUDENT_AUTH_TEST_PG points at a LOCAL server."""
import pytest
from sqlalchemy import create_engine, text

from tests import test_student_id_allocation_pg as base

pytestmark = base.pytestmark          # skipped without a local disposable PostgreSQL (STUDENT_AUTH_TEST_PG)
db_url = base.db_url                  # the same throwaway-database fixture
_alembic = base._alembic


def _live_like(url, ids):
    """A live-like database BEFORE 0003: built from the models like the allocator tests (which now declare date_of_birth), so the
    column is removed again - a database that has not run 0003 does not have it."""
    base._live_like(url, ids)
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("alter table public.students drop column date_of_birth"))
    engine.dispose()


def _run(url, *args):
    r = _alembic(url, *args)
    assert r.returncode == 0, r.stderr[-2000:]


def _q(url, sql):
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            result = c.execute(text(sql))
            return result.all() if result.returns_rows else []
    finally:
        engine.dispose()


def _column(url):
    rows = _q(url, "select data_type, is_nullable, column_default from information_schema.columns "
                   "where table_schema = 'public' and table_name = 'students' and column_name = 'date_of_birth'")
    return rows[0] if rows else None


def _head(url):
    return _q(url, "select version_num from public.alembic_version")[0][0]


def _students(url):
    return _q(url, "select student_id, first_name, last_name, email, status from public.students order by student_id")


def _types(url):
    return dict(_q(url, "select table_name || '.' || column_name, data_type || coalesce('(' || character_maximum_length || ')', '') "
                        "from information_schema.columns where table_schema = 'public' "
                        "and table_name in ('students', 'password_setup_reset_tokens', 'student_id_allocator')"))


def test_a_new_empty_database_reaches_head_with_varchar_student_ids(db_url):
    # the corrected roots (b9149bb383de, 0001_create_students) create students.student_id as VARCHAR(20), so 562cc1f6c4b7's foreign key
    # can be created and the whole chain runs from an empty database
    _run(db_url, "upgrade", "head")
    assert _head(db_url) == "0003_students_date_of_birth"
    types = _types(db_url)
    assert types["students.student_id"] == types["password_setup_reset_tokens.student_id"] == "character varying(20)"
    assert _column(db_url) == ("date", "YES", None)
    assert _q(db_url, "select last_value from public.student_id_allocator where name = 'STU'")[0][0] == 100


def test_a_new_database_has_the_same_schema_as_an_existing_one(db_url):
    # an existing database (students in the live shape, recorded at the merge 7ada309b2310, then 562cc1f6c4b7 .. head) and a new one
    _run(db_url, "upgrade", "head")
    fresh = _types(db_url)
    other = db_url.set(database=db_url.database + "_existing")
    admin = create_engine(db_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'create database "{other.database}"'))
    try:
        from app.models.student import Student
        engine = create_engine(other)
        Student.__table__.create(engine)
        with engine.begin() as c:
            c.execute(text("create table alembic_version (version_num varchar(32) primary key)"))
            c.execute(text("insert into alembic_version values ('7ada309b2310')"))
        engine.dispose()
        _run(other, "upgrade", "head")
        assert _types(other) == fresh
    finally:
        with admin.connect() as c:
            c.execute(text(f'drop database if exists "{other.database}" with (force)'))
        admin.dispose()


def test_the_model_matches_date_of_birth(db_url):
    # alembic check must never propose dropping students.date_of_birth (the model declares it)
    _run(db_url, "upgrade", "head")
    r = _alembic(db_url, "check")
    assert "date_of_birth" not in r.stdout + r.stderr, (r.stdout + r.stderr)[-1500:]


def test_a_new_database_in_the_live_shape_gets_the_column(db_url):
    # newly created, built like the live database and the E2E harness (VARCHAR(20) IDs), recorded at 0002: Alembic applies 0003
    _live_like(db_url, [])
    _run(db_url, "upgrade", "0002_student_id_allocator")
    assert _column(db_url) is None
    _run(db_url, "upgrade", "head")
    assert _head(db_url) == "0003_students_date_of_birth" and _column(db_url) == ("date", "YES", None)


def test_an_existing_database_is_upgraded_and_keeps_every_student(db_url):
    _live_like(db_url, ["STU000001", "STU000002"])
    assert _column(db_url) is None
    before = _students(db_url)
    _run(db_url, "upgrade", "head")
    assert _head(db_url) == "0003_students_date_of_birth" and _column(db_url) == ("date", "YES", None)
    assert _students(db_url) == before
    assert _q(db_url, "select count(*) from public.students where date_of_birth is null")[0][0] == 2


def test_a_legacy_column_with_values_is_adopted_unchanged(db_url):
    _live_like(db_url, ["STU000001"])
    _q(db_url, "alter table public.students add column date_of_birth date")
    _q(db_url, "update public.students set date_of_birth = '2008-04-01'")
    _run(db_url, "upgrade", "head")
    assert _head(db_url) == "0003_students_date_of_birth"
    assert [str(r[0]) for r in _q(db_url, "select date_of_birth from public.students")] == ["2008-04-01"]


@pytest.mark.parametrize("ddl, message", [
    ("add column date_of_birth varchar(10)", "type VARCHAR(10) (expected DATE)"),
    ("add column date_of_birth date not null default '2000-01-01'", "NOT NULL"),
    ("add column date_of_birth date default current_date", "default"),
])
def test_an_existing_column_of_another_shape_stops_the_upgrade(db_url, ddl, message):
    _live_like(db_url, ["STU000001"])
    _run(db_url, "upgrade", "0002_student_id_allocator")
    _q(db_url, f"alter table public.students {ddl}")
    r = _alembic(db_url, "upgrade", "head")
    assert r.returncode != 0 and message in r.stderr, r.stderr[-1500:]
    assert _head(db_url) == "0002_student_id_allocator"


def test_downgrade_and_upgrade_again(db_url):
    _live_like(db_url, ["STU000001"])
    _run(db_url, "upgrade", "head")
    _run(db_url, "downgrade", "0002_student_id_allocator")
    assert _column(db_url) is None and _head(db_url) == "0002_student_id_allocator"
    _run(db_url, "upgrade", "head")
    assert _column(db_url) == ("date", "YES", None)
