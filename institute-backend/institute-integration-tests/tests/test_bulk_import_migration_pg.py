"""Paper migration 0013_bulk_imports on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server):
the three tables are created on a database at 0012 and adopted when the baseline already made them; their CHECKs hold; the downgrade
refuses while imports exist. Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_marking_migrations_pg import BRANCH, PROVIDER, SUBJECT, seed_whole_number_data
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, pytestmark, run, sql  # noqa: F401

TABLES = ("bulk_import_images", "bulk_import_jobs", "bulk_import_rows")


def tables(db) -> list[str]:
    return [r[0] for r in sql(db, "select table_name from information_schema.tables where table_name like 'bulk_import_%' order by 1")]


def job(db, status: str = "UPLOADED") -> str:
    return sql(db, "insert into bulk_import_jobs(job_id, provider_id, branch_id, subject_id, status, file_format, file_name, source_size, "
                   f"source_sha256, created_by) values (gen_random_uuid(), '{PROVIDER}', '{BRANCH}', '{SUBJECT}', '{status}', 'XLSX', "
                   f"'q.xlsx', 1, '{'a' * 64}', gen_random_uuid()) returning job_id")[0][0]


def test_created_on_0012_and_their_checks_hold(db):
    paper_at(db, "0012_object_storage_keys")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    for t in TABLES:
        sql(db, f"drop table if exists {t} cascade")              # the shape of a database migrated before 0013
    seed_whole_number_data(db)
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0018_question_bank_search" and tables(db) == sorted([*TABLES, "bulk_import_uploads"])   # 0016 adds the resumable upload sessions
    job_id = job(db)
    sql(db, f"insert into bulk_import_rows(job_id, row_no, data) values ('{job_id}', 2, '{{}}')")
    with pytest.raises(IntegrityError):
        job(db, status="DONE")                                     # unknown job status
    with pytest.raises(IntegrityError):
        sql(db, f"insert into bulk_import_rows(job_id, row_no, data) values ('{job_id}', 2, '{{}}')")     # one record per row
    with pytest.raises(IntegrityError):
        sql(db, f"insert into bulk_import_rows(job_id, row_no, status, data) values ('{job_id}', 3, 'MAYBE', '{{}}')")


def test_a_new_database_adopts_the_baseline_tables(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    assert head(db) == PAPER_HEAD and tables(db) == sorted([*TABLES, "bulk_import_uploads"])   # 0016 adds the resumable upload sessions


def test_downgrade_refuses_while_imports_exist(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    seed_whole_number_data(db)
    job(db)
    refused = alembic(db, "paper", "downgrade", "0012_object_storage_keys")
    assert refused.returncode != 0 and "Downgrade refused" in refused.stderr
    sql(db, "delete from bulk_import_jobs")
    run(db, "paper", "downgrade", "0012_object_storage_keys")
    assert tables(db) == []
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD
