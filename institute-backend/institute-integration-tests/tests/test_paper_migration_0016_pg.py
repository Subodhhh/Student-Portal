"""Paper migration 0016_resumable_bulk_uploads on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL
server): existing jobs keep their values, source_size holds files over 2 GiB, the upload-session and notification-outbox tables are
there, and the downgrade refuses while a session or an over-2-GiB job exists. Skipped on the default SQLite run."""
import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_marking_migrations_pg import BRANCH, PROVIDER, SUBJECT, seed_whole_number_data
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, run, sql  # noqa: F401
from tests.test_paper_migration_0006_pg import pytestmark  # noqa: F401

BEFORE = "0015_question_types"
GIB = 1024 ** 3


def at_0015_with_a_job(db) -> None:
    paper_at(db, BEFORE)
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    seed_whole_number_data(db)
    sql(db, f"insert into bulk_import_jobs(job_id, scope, provider_id, branch_id, subject_id, file_format, file_name, source_size, "
            f"source_sha256, created_by) values (gen_random_uuid(), 'PROVIDER', '{PROVIDER}', '{BRANCH}', '{SUBJECT}', 'XLSX', 'q.xlsx', "
            f"12345, '{'a' * 64}', gen_random_uuid())")


def session(db, size: int = 10, received: int = 0) -> None:
    sql(db, f"insert into bulk_import_uploads(upload_id, scope, provider_id, subject_id, file_format, file_name, fingerprint, size, "
            f"chunk_size, received_bytes, created_by) values (gen_random_uuid(), 'PROVIDER', '{PROVIDER}', '{SUBJECT}', 'XLSX', "
            f"'q.xlsx', 'q.xlsx|{size}|1', {size}, 8, {received}, gen_random_uuid())")


def test_upgrade_keeps_jobs_and_allows_files_over_two_gib(db):
    at_0015_with_a_job(db)
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0018_question_bank_search"
    assert sql(db, "select source_size from bulk_import_jobs") == [(12345,)]                     # kept
    sql(db, f"update bulk_import_jobs set source_size = {3 * GIB}")                              # over 2 GiB fits now
    assert sql(db, "select data_type from information_schema.columns where table_name = 'bulk_import_jobs' "
                   "and column_name = 'source_size'") == [("bigint",)]
    session(db, size=3 * GIB, received=8)
    with pytest.raises(IntegrityError):
        session(db, size=10, received=11)                                                        # never more than the file
    sql(db, "insert into paper_notification_outbox(outbox_id, dedupe_key, payload) values (gen_random_uuid(), 'k', '{}')")
    with pytest.raises(IntegrityError):
        sql(db, "insert into paper_notification_outbox(outbox_id, dedupe_key, payload) values (gen_random_uuid(), 'k', '{}')")


def test_downgrade_refuses_while_a_session_or_a_large_file_exists(db):
    at_0015_with_a_job(db)
    run(db, "paper", "upgrade", "head")
    session(db)
    refused = alembic(db, "paper", "downgrade", BEFORE)
    assert refused.returncode != 0 and "Downgrade refused" in refused.stderr and head(db) == PAPER_HEAD
    sql(db, "delete from bulk_import_uploads")
    sql(db, f"update bulk_import_jobs set source_size = {3 * GIB}")
    refused = alembic(db, "paper", "downgrade", BEFORE)
    assert refused.returncode != 0 and "Downgrade refused" in refused.stderr
    sql(db, "update bulk_import_jobs set source_size = 1")
    run(db, "paper", "downgrade", BEFORE)
    assert head(db) == BEFORE
    run(db, "paper", "upgrade", "head")                                                          # and up again
