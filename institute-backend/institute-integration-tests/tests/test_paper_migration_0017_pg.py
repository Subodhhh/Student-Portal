"""Paper migration 0017_taxonomy_aliases on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server):
existing provider records keep their values, the platform list (provider_id NULL) gets its own unique names, a MERGED record must
name the record it was merged into, one pending request per spelling, and the downgrade refuses while platform records, merged
records or requests exist. Skipped on the default SQLite run."""
import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_marking_migrations_pg import PROVIDER, SUBJECT, seed_whole_number_data
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, run, sql  # noqa: F401
from tests.test_paper_migration_0006_pg import pytestmark  # noqa: F401

BEFORE = "0016_resumable_bulk_uploads"


def at_0016_with_a_chapter(db) -> str:
    paper_at(db, BEFORE)
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    seed_whole_number_data(db)
    return chapter(db, "Laws of Motion", provider=f"'{PROVIDER}'")


def chapter(db, name: str, provider: str = "null", status: str = "ACTIVE", merged_into: str = "null") -> str:
    return sql(db, f"insert into question_taxonomy_nodes(node_id, provider_id, subject_id, parent_id, level, name, name_key, status, "
                   f"created_by{', merged_into_id' if merged_into != 'null' else ''}) values (gen_random_uuid(), {provider}, '{SUBJECT}', "
                   f"null, 'CHAPTER', '{name}', lower('{name}'), '{status}', gen_random_uuid()"
                   f"{', ' + merged_into if merged_into != 'null' else ''}) returning node_id")[0][0]


def request(db, node: str, alias: str = "Newtons Laws") -> None:
    sql(db, f"insert into question_taxonomy_alias_requests(request_id, node_id, provider_id, subject_id, alias, alias_key, source, "
            f"status, requested_by) values (gen_random_uuid(), '{node}', null, '{SUBJECT}', '{alias}', lower('{alias}'), 'QUESTION', "
            f"'PENDING', gen_random_uuid())")


def test_upgrade_keeps_records_and_adds_the_platform_list_merging_and_requests(db):
    provider_chapter = at_0016_with_a_chapter(db)
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0017_taxonomy_aliases"
    assert sql(db, f"select provider_id, name, status, merged_into_id from question_taxonomy_nodes where node_id = '{provider_chapter}'") \
        == [(__import__("uuid").UUID(PROVIDER), "Laws of Motion", "ACTIVE", None)]
    platform = chapter(db, "Laws of Motion")                                  # the platform list is separate from the provider's
    with pytest.raises(IntegrityError):
        chapter(db, "Laws of Motion")                                         # but unique within itself
    with pytest.raises(IntegrityError):
        chapter(db, "Duplicate", status="MERGED")                             # MERGED must say into which record
    with pytest.raises(IntegrityError):
        chapter(db, "Other", merged_into=f"'{platform}'")                      # and only a MERGED record says so
    chapter(db, "Newtons Laws", status="MERGED", merged_into=f"'{platform}'")
    request(db, platform)
    with pytest.raises(IntegrityError):
        request(db, platform)                                                  # one pending request per spelling


def test_downgrade_refuses_while_platform_records_or_requests_exist(db):
    at_0016_with_a_chapter(db)
    run(db, "paper", "upgrade", "head")
    platform = chapter(db, "Gravitation")
    request(db, platform)
    refused = alembic(db, "paper", "downgrade", BEFORE)
    assert refused.returncode != 0 and "Downgrade refused" in refused.stderr and head(db) == PAPER_HEAD
    sql(db, "delete from question_taxonomy_alias_requests")
    sql(db, f"delete from question_taxonomy_nodes where node_id = '{platform}'")
    run(db, "paper", "downgrade", BEFORE)
    assert head(db) == BEFORE
    assert sql(db, "select is_nullable from information_schema.columns where table_name = 'question_taxonomy_nodes' "
                   "and column_name = 'provider_id'") == [("NO",)]
    run(db, "paper", "upgrade", "head")                                        # and up again
