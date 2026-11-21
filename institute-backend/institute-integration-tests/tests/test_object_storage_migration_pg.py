"""Paper migration 0012_object_storage_keys on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server):
existing database pictures keep working unchanged; the database enforces "db pictures have no key, object pictures always have one";
the downgrade refuses while object-stored pictures exist. Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_marking_migrations_pg import PROVIDER, seed_whole_number_data
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, pytestmark, run, sql  # noqa: F401


def picture(db, storage: str, key: str | None, checksum: str) -> None:
    key_sql = f"'{key}'" if key else "null"
    data_sql = "'\\x89'::bytea" if storage == "db" else "null"
    sql(db, "insert into question_image_assets(asset_id, provider_id, file_name, mime_type, file_size, width, height, checksum, storage, "
            f"image_data, storage_key, created_by) values (gen_random_uuid(), '{PROVIDER}', 'p.png', 'image/png', 1, 1, 1, '{checksum}', "
            f"'{storage}', {data_sql}, {key_sql}, gen_random_uuid())")


def at_0011_with_a_database_picture(db) -> None:
    paper_at(db, "0011_question_taxonomy")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    sql(db, "alter table question_image_assets drop constraint if exists question_image_assets_storage_key_check")
    sql(db, "alter table question_image_assets drop column if exists storage_key")       # the shape before 0012
    sql(db, "alter table question_image_assets drop constraint if exists question_image_assets_storage_check")
    sql(db, "alter table question_image_assets add constraint question_image_assets_storage_check check (storage in ('db'))")
    seed_whole_number_data(db)
    sql(db, "insert into question_image_assets(asset_id, provider_id, file_name, mime_type, file_size, width, height, checksum, storage, "
            f"image_data, created_by) values (gen_random_uuid(), '{PROVIDER}', 'p.png', 'image/png', 1, 1, 1, '{'a' * 64}', 'db', "
            "'\\x89'::bytea, gen_random_uuid())")


def test_existing_pictures_survive_and_the_new_rules_hold(db):
    at_0011_with_a_database_picture(db)
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0015_question_types"
    assert sql(db, "select storage, storage_key, image_data is not null from question_image_assets") == [("db", None, True)]
    picture(db, "s3", f"question-images/{PROVIDER}/{'b' * 64}", "b" * 64)               # object-stored: allowed with its key
    for storage, key in (("s3", None), ("local", None), ("db", "question-images/x"), ("ftp", "k")):
        with pytest.raises(IntegrityError):
            picture(db, storage, key, "c" * 64)


def test_downgrade_refuses_while_object_pictures_exist(db):
    at_0011_with_a_database_picture(db)
    run(db, "paper", "upgrade", "head")
    picture(db, "local", f"question-images/{PROVIDER}/{'d' * 64}", "d" * 64)
    refused = alembic(db, "paper", "downgrade", "0011_question_taxonomy")
    assert refused.returncode != 0 and "object storage" in refused.stderr
    sql(db, "delete from question_image_assets where storage <> 'db'")
    run(db, "paper", "downgrade", "0011_question_taxonomy")
    assert sql(db, "select count(*) from information_schema.columns where table_name = 'question_image_assets' "
                   "and column_name = 'storage_key'") == [(0,)]
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD
