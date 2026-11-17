"""Paper migration 0008_question_images on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server).

  * a NEW database: every Institute chain to head - the baseline already creates question_image_assets and the picture columns from the
    models, and 0008 adopts them;
  * a database at 0007 (before pictures): 0008 adds the table and five nullable columns; existing questions keep NULL pictures;
  * downgrade and upgrade again.
Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, db, head, paper_at, pytestmark, run, sql  # noqa: F401  (db: fixture; pytestmark: PostgreSQL only)

COLUMNS = ["option_a_image_id", "option_b_image_id", "option_c_image_id", "option_d_image_id", "stem_image_id"]


def picture_columns(db) -> list[tuple]:
    return sql(db, "select column_name, is_nullable, data_type from information_schema.columns where table_name = 'question_versions' "
                   "and column_name like '%image_id' order by column_name")


def keys(db) -> set[str]:
    return {r[0] for r in sql(db, "select conname from pg_constraint where conrelid = 'question_versions'::regclass and contype = 'f'")}


def test_a_new_database_migrates_every_chain_to_head(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0009_picture_recognition_jobs"
    assert [c[0] for c in picture_columns(db)] == COLUMNS and all(c[1] == "YES" and c[2] == "uuid" for c in picture_columns(db))
    assert {f"question_versions_{c}_fkey" for c in COLUMNS} <= keys(db)


def test_a_database_from_before_pictures_gets_them_and_keeps_its_questions(db):
    paper_at(db, "0007_paper_review_drafts")
    sql(db, "drop table if exists question_image_assets cascade")
    for c in COLUMNS:
        sql(db, f"alter table question_versions drop column if exists {c}")          # the shape of a database migrated before pictures
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD and [c[0] for c in picture_columns(db)] == COLUMNS
    assert {f"question_versions_{c}_fkey" for c in COLUMNS} <= keys(db)
    assert sql(db, "select count(*) from question_image_assets") == [(0,)]


def test_downgrade_and_upgrade_again(db):
    paper_at(db, "head")
    run(db, "paper", "downgrade", "0007_paper_review_drafts")
    assert picture_columns(db) == [] and head(db) == "0007_paper_review_drafts"
    run(db, "paper", "upgrade", "head")
    assert [c[0] for c in picture_columns(db)] == COLUMNS and head(db) == PAPER_HEAD


def test_0009_adds_the_recognition_job_table_and_downgrades_cleanly(db):
    paper_at(db, "0008_question_images")
    sql(db, "drop table if exists picture_recognition_jobs")               # a database migrated before the job table
    run(db, "paper", "upgrade", "head")
    cols = {r[0] for r in sql(db, "select column_name from information_schema.columns where table_name = 'picture_recognition_jobs'")}
    assert head(db) == PAPER_HEAD and {"asset_id", "recognizer", "version", "status", "attempts", "locked_until", "result"} <= cols
    run(db, "paper", "downgrade", "0008_question_images")
    assert sql(db, "select to_regclass('public.picture_recognition_jobs') is null") == [(True,)]
