"""Paper migration 0007_paper_review_drafts on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server).

  * a NEW database: every Institute chain to head - the paper baseline already creates paper_review_drafts from the models, and 0007
    adopts it;
  * a database at 0006 without the table (migrated before it joined the models): 0007 creates it;
  * an existing table of another shape (a missing column or partial unique index): the upgrade stops, the database stays at 0006;
  * the partial unique indexes: one OPEN draft per person per file (IMPORT) and per paper version (CORRECTION) - closed drafts never
    block a new one; the CHECK constraints refuse an IMPORT draft without its file and an unknown status;
  * downgrade and upgrade again.
Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, run, sql  # noqa: F401  (db is a fixture)

pytestmark = pytest.mark.skipif(not __import__("os").environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
                                reason="needs a disposable PostgreSQL (TEST_DATABASE_URL)")
BEFORE = "0006_upload_recognition"
HEAD = PAPER_HEAD                     # upgrades go to the chain head (0008 follows 0007)
UNIQUE = {"ux_paper_review_drafts_open_import", "ux_paper_review_drafts_open_correction"}


def table(url):
    cols = [r[0] for r in sql(url, "select column_name from information_schema.columns where table_schema = 'public' "
                                   "and table_name = 'paper_review_drafts' order by ordinal_position")]
    idx = {r[0]: r[1] for r in sql(url, "select indexname, indexdef from pg_indexes where tablename = 'paper_review_drafts'")}
    return cols, idx


def test_a_new_database_migrates_every_chain_to_head(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    cols, idx = table(db)
    assert head(db) == HEAD and len(cols) == 26 and UNIQUE <= set(idx)
    assert "WHERE (((status)::text = 'OPEN'::text) AND ((kind)::text = 'IMPORT'::text))" in idx["ux_paper_review_drafts_open_import"]


def test_a_database_from_before_the_table_gets_it(db):
    paper_at(db, BEFORE)
    sql(db, "drop table paper_review_drafts")                                  # the shape of a database migrated before engine 2.4
    run(db, "paper", "upgrade", "head")
    cols, idx = table(db)
    assert head(db) == HEAD and len(cols) == 26 and UNIQUE <= set(idx) and "ix_paper_review_drafts_branch_status" in idx


def test_the_baseline_table_is_adopted_unchanged(db):
    paper_at(db, BEFORE)
    before = table(db)
    assert before[0]                                                            # created by 0001 from the current models
    run(db, "paper", "upgrade", "head")
    assert head(db) == HEAD and table(db) == before


@pytest.mark.parametrize("change, message", [
    ("drop index ux_paper_review_drafts_open_import", "missing unique indexes"),
    ("drop column updated_ip", "columns differ"),
])
def test_an_existing_table_of_another_shape_stops_the_upgrade(db, change, message):
    paper_at(db, BEFORE)
    sql(db, change if change.startswith("drop index") else f"alter table paper_review_drafts {change}")
    r = alembic(db, "paper", "upgrade", "head")
    assert r.returncode != 0 and message in r.stderr, r.stderr[-1500:]
    assert head(db) == BEFORE                                                   # nothing recorded as applied


def _draft(url, provider, branch, subject, staff, **kw):
    """Insert a draft directly (the constraints, not the API, are under test); kw overrides columns."""
    file_id = kw.pop("file_id", None)
    if file_id is None and kw.get("kind", "IMPORT") == "IMPORT":
        file_id = uuid.uuid4()
        sql(url, f"insert into stored_files (file_id, provider_id, storage_key, original_name, content_type, size_bytes, sha256, uploaded_by) "
                 f"values ('{file_id}', '{provider}', '{file_id.hex}', 'p.csv', 'text/csv', 1, 'x', '{staff}')")
    row = {"draft_id": uuid.uuid4(), "kind": "IMPORT", "provider_id": provider, "branch_id": branch, "subject_id": subject,
           "file_id": file_id, "file_ext": ".csv", "review_token": "t" * 64, "engine_version": "2.4.0", "decisions": "{}",
           "status": "OPEN", "created_by": staff, "updated_by": staff, **kw}
    cols = ", ".join(row)
    vals = ", ".join("null" if v is None else f"'{v}'" for v in row.values())
    sql(url, f"insert into paper_review_drafts ({cols}) values ({vals})")
    return row["draft_id"]


def _scope(url):
    provider, branch, subject = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sql(url, f"insert into providers (provider_id, name, email, status, created_at, updated_at) "
             f"values ('{provider}', 'P', 'p@example.com', 'ACTIVE', now(), now())")
    sql(url, f"insert into branches (branch_id, provider_id, name, status, created_at, updated_at) "
             f"values ('{branch}', '{provider}', 'B', 'ACTIVE', now(), now())")
    sql(url, f"insert into subjects (subject_id, code, name) values ('{subject}', 'PHY{subject.hex[:4]}', 'Physics')")
    return provider, branch, subject


def test_one_open_draft_per_person_per_file_and_per_paper_version(db):
    paper_at(db, "head")
    provider, branch, subject = _scope(db)
    me, you = uuid.uuid4(), uuid.uuid4()
    first = _draft(db, provider, branch, subject, me)
    with pytest.raises(IntegrityError):
        _draft(db, provider, branch, subject, me)                               # the same file, the same person, both OPEN
    _draft(db, provider, branch, subject, you)                                  # another person: their own draft
    _draft(db, provider, branch, subject, me, review_token="u" * 64)            # another file
    sql(db, f"update paper_review_drafts set status = 'IMPORTED' where draft_id = '{first}'")
    _draft(db, provider, branch, subject, me)                                   # a closed draft never blocks a new one
    # CORRECTION drafts: one OPEN per person per paper version (the version is referenced, so it must exist)
    paper, pv = uuid.uuid4(), uuid.uuid4()
    sql(db, f"insert into papers (paper_id, scope, provider_id, branch_id, subject_id, title, created_by) "
            f"values ('{paper}', 'PROVIDER', '{provider}', '{branch}', '{subject}', 'T', '{me}')")
    sql(db, f"insert into paper_versions (paper_version_id, paper_id, version_no, status, question_count, total_marks, content_hash, "
            f"created_by) values ('{pv}', '{paper}', 1, 'VALIDATED', 1, 4, '{'h' * 64}', '{me}')")
    _draft(db, provider, branch, subject, me, kind="CORRECTION", paper_version_id=pv, review_token=None, file_ext=None)
    with pytest.raises(IntegrityError):
        _draft(db, provider, branch, subject, me, kind="CORRECTION", paper_version_id=pv, review_token=None, file_ext=None)


@pytest.mark.parametrize("kw", [{"file_id": "null"}, {"status": "PENDING"}, {"kind": "OTHER"}, {"revision": 0}])
def test_the_constraints_refuse_an_inconsistent_draft(db, kw):
    paper_at(db, "head")
    provider, branch, subject = _scope(db)
    staff = uuid.uuid4()
    if kw.get("file_id") == "null":
        with pytest.raises(IntegrityError):                                     # an IMPORT draft without its stored file
            row = f"'{uuid.uuid4()}', 'IMPORT', '{provider}', '{branch}', '{subject}', '.csv', '{'t' * 64}', '2.4.0', '{{}}', '{staff}', '{staff}'"
            sql(db, "insert into paper_review_drafts (draft_id, kind, provider_id, branch_id, subject_id, file_ext, review_token, "
                    f"engine_version, decisions, created_by, updated_by) values ({row})")
        return
    with pytest.raises(IntegrityError):
        _draft(db, provider, branch, subject, staff, **kw)


def test_downgrade_and_upgrade_again(db):
    paper_at(db, "head")
    run(db, "paper", "downgrade", BEFORE)
    assert table(db) == ([], {}) and head(db) == BEFORE
    run(db, "paper", "upgrade", "head")
    assert head(db) == HEAD and UNIQUE <= set(table(db)[1])
