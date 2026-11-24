"""Paper migration 0011_question_taxonomy on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server):

  * a database at 0010 with questions: the two tables and three nullable link columns are added, existing labels untouched;
  * the partial unique indexes stop duplicate names per place in the tree in the database itself (not only in the service);
  * a NEW database: the baseline already creates the tables from the models and 0011 adopts them;
  * downgrade refuses while records exist, then works and re-upgrades.
Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_marking_migrations_pg import PROVIDER, QUESTION, SUBJECT, seed_whole_number_data
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, pytestmark, run, sql  # noqa: F401

LINKS = ["chapter_id", "subtopic_id", "topic_id"]


def link_columns(db) -> list[tuple]:
    return sql(db, "select column_name, is_nullable from information_schema.columns where table_name = 'bank_questions' "
                   "and column_name in ('chapter_id', 'topic_id', 'subtopic_id') order by column_name")


def chapter(db, name_key: str, parent: str | None = None, level: str = "CHAPTER") -> str:
    parent_sql = f"'{parent}'" if parent else "null"
    return sql(db, f"insert into question_taxonomy_nodes(node_id, provider_id, subject_id, parent_id, level, name, name_key, created_by) "
                   f"values (gen_random_uuid(), '{PROVIDER}', '{SUBJECT}', {parent_sql}, '{level}', '{name_key}', '{name_key}', "
                   f"gen_random_uuid()) returning node_id")[0][0]


def test_upgrade_from_0010_adds_the_taxonomy_and_keeps_question_labels(db):
    paper_at(db, "0010_optional_question_marks")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    sql(db, "drop table if exists question_taxonomy_alias_requests")            # (0017; the baseline makes it from today's models)
    sql(db, "drop table if exists question_taxonomy_aliases")                   # the shape of a database migrated before 0011
    sql(db, "alter table bank_questions drop column if exists chapter_id, drop column if exists topic_id, drop column if exists subtopic_id")
    sql(db, "drop table if exists question_taxonomy_nodes")
    seed_whole_number_data(db)
    sql(db, f"update bank_questions set chapter = 'Laws of Motion' where question_id = '{QUESTION}'")
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0018_question_bank_search"
    assert link_columns(db) == [(c, "YES") for c in LINKS]
    assert sql(db, "select chapter, chapter_id from bank_questions") == [("Laws of Motion", None)]     # untouched until the backfill

    first = chapter(db, "laws of motion")
    with pytest.raises(IntegrityError):
        chapter(db, "laws of motion")                                         # same name, same provider/subject: refused by the index
    chapter(db, "force", parent=first, level="TOPIC")
    with pytest.raises(IntegrityError):
        chapter(db, "force", parent=first, level="TOPIC")                     # same name under the same parent
    with pytest.raises(IntegrityError):
        chapter(db, "orphan", level="TOPIC")                                  # a topic without a parent breaks the hierarchy check


def test_a_new_database_adopts_the_baseline_tables(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    assert head(db) == PAPER_HEAD and link_columns(db) == [(c, "YES") for c in LINKS]
    assert sql(db, "select to_regclass('public.question_taxonomy_aliases') is not null") == [(True,)]


def test_downgrade_refuses_while_records_exist(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    seed_whole_number_data(db)
    chapter(db, "optics")
    refused = alembic(db, "paper", "downgrade", "0010_optional_question_marks")
    assert refused.returncode != 0 and "Downgrade refused" in refused.stderr
    sql(db, "delete from question_taxonomy_nodes")
    run(db, "paper", "downgrade", "0010_optional_question_marks")
    assert link_columns(db) == [] and sql(db, "select to_regclass('public.question_taxonomy_nodes') is null") == [(True,)]
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD
