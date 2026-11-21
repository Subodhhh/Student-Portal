"""Paper migration 0014_three_scope_question_bank on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL
server): existing data upgrades unchanged, the new shapes (provider-wide questions, platform pictures, GLOBAL / provider-wide import
jobs) are accepted while inconsistent ones are refused, a new database adopts the baseline's shapes, and the downgrade refuses while a
new shape exists. Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_marking_migrations_pg import BRANCH, PROVIDER, QUESTION, SUBJECT, seed_whole_number_data
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, run, sql  # noqa: F401
from tests.test_paper_migration_0006_pg import pytestmark  # noqa: F401

BEFORE = "0013_bulk_imports"


def at_0013_with_data(db) -> None:
    paper_at(db, BEFORE)
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    seed_whole_number_data(db)


def question(db, provider: str | None, branch: str | None, scope: str = "PROVIDER") -> None:
    p, b = (f"'{provider}'" if provider else "null"), (f"'{branch}'" if branch else "null")
    sql(db, f"insert into bank_questions(question_id, scope, subject_id, owner_staff_id, provider_id, branch_id) "
            f"values (gen_random_uuid(), '{scope}', '{SUBJECT}', gen_random_uuid(), {p}, {b})")


def picture(db, provider: str | None, checksum: str) -> None:
    p = f"'{provider}'" if provider else "null"
    sql(db, f"insert into question_image_assets(asset_id, provider_id, file_name, mime_type, file_size, width, height, checksum, storage, "
            f"storage_key, created_by) values (gen_random_uuid(), {p}, 'a.png', 'image/png', 1, 1, 1, '{checksum}', 'local', "
            f"'question-images/x/{checksum}', gen_random_uuid())")


def job(db, scope: str, provider: str | None, branch: str | None) -> None:
    p, b = (f"'{provider}'" if provider else "null"), (f"'{branch}'" if branch else "null")
    sql(db, f"insert into bulk_import_jobs(job_id, scope, provider_id, branch_id, subject_id, file_format, file_name, source_size, "
            f"source_sha256, created_by) values (gen_random_uuid(), '{scope}', {p}, {b}, '{SUBJECT}', 'XLSX', 'q.xlsx', 1, "
            f"'{'a' * 64}', gen_random_uuid())")


def test_upgrade_keeps_existing_rows_and_accepts_the_three_scopes(db):
    at_0013_with_data(db)
    sql(db, f"insert into bulk_import_jobs(job_id, provider_id, branch_id, subject_id, file_format, file_name, source_size, source_sha256, "
            f"created_by) values (gen_random_uuid(), '{PROVIDER}', '{BRANCH}', '{SUBJECT}', 'XLSX', 'q.xlsx', 1, '{'b' * 64}', gen_random_uuid())")
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0015_question_types"
    # existing rows are untouched: the branch question keeps its (now: originating) branch, the old job becomes a PROVIDER job
    assert sql(db, f"select provider_id::text, branch_id::text from bank_questions where question_id = '{QUESTION}'") == [(PROVIDER, BRANCH)]
    assert sql(db, "select scope, branch_id::text from bulk_import_jobs") == [("PROVIDER", BRANCH)]

    question(db, PROVIDER, None)                         # provider-wide
    question(db, None, None, scope="GLOBAL")             # platform
    with pytest.raises(IntegrityError):
        question(db, None, BRANCH)                       # a PROVIDER question needs its provider
    with pytest.raises(IntegrityError):
        question(db, PROVIDER, None, scope="GLOBAL")     # a GLOBAL question has no provider

    picture(db, PROVIDER, "c" * 64)
    picture(db, None, "c" * 64)                          # the same bytes as a platform picture: a separate owner
    with pytest.raises(IntegrityError):
        picture(db, None, "c" * 64)                      # one platform picture per checksum

    job(db, "GLOBAL", None, None)
    job(db, "PROVIDER", PROVIDER, None)
    with pytest.raises(IntegrityError):
        job(db, "GLOBAL", PROVIDER, None)
    with pytest.raises(IntegrityError):
        job(db, "PROVIDER", None, None)
    with pytest.raises(IntegrityError):
        job(db, "BRANCH", PROVIDER, BRANCH)


def test_a_new_database_adopts_the_baseline_shapes(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    assert head(db) == PAPER_HEAD
    names = {r[0] for r in sql(db, "select indexname from pg_indexes where indexname in ('ix_bank_questions_provider_subject', "
                                   "'uq_question_image_assets_platform_checksum', 'ix_bulk_import_jobs_owner_created')")}
    assert len(names) == 3
    run(db, "paper", "downgrade", BEFORE)               # empty database: the downgrade restores the old shapes
    run(db, "paper", "upgrade", "head")                  # and the upgrade runs again on them


def test_downgrade_refuses_while_a_new_shape_exists(db):
    at_0013_with_data(db)
    run(db, "paper", "upgrade", "head")
    for make, undo in ((lambda: question(db, PROVIDER, None), "delete from bank_questions where branch_id is null"),
                       (lambda: picture(db, None, "d" * 64), "delete from question_image_assets where provider_id is null"),
                       (lambda: job(db, "GLOBAL", None, None), "delete from bulk_import_jobs")):
        make()
        refused = alembic(db, "paper", "downgrade", BEFORE)
        assert refused.returncode != 0 and "Downgrade refused" in refused.stderr
        assert head(db) == PAPER_HEAD                   # nothing changed
        sql(db, undo)
    run(db, "paper", "downgrade", BEFORE)
    assert head(db) == BEFORE
    with pytest.raises(IntegrityError):
        question(db, PROVIDER, None)                     # the old rule is back: a PROVIDER question needs its branch
    run(db, "paper", "upgrade", "head")
