"""Paper migration 0015_question_types on REAL PostgreSQL (each test in its own throwaway database on the TEST_DATABASE_URL server):
existing MCQ versions upgrade unchanged (type MCQ_SINGLE, same values), the three shapes are enforced, versions and picture links
stay append-only, a new database adopts the baseline's shapes, and the downgrade refuses while MSQ / NAT data exists.
Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import json

import pytest
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

from tests.test_marking_migrations_pg import QUESTION, SUBJECT, seed_whole_number_data
from tests.test_paper_migration_0006_pg import AFTER_PAPER, PAPER_HEAD, alembic, db, head, paper_at, run, sql  # noqa: F401
from tests.test_paper_migration_0006_pg import pytestmark  # noqa: F401

BEFORE = "0014_three_scope_question_bank"
MSQ_CONTENT = json.dumps({"options": [{"id": "A", "text": "1", "image": None}, {"id": "B", "text": "2", "image": None}]})


def at_0014_with_data(db) -> None:
    paper_at(db, BEFORE)
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    seed_whole_number_data(db)


def typed_question(db, qtype: str) -> str:
    return sql(db, f"insert into bank_questions(question_id, scope, subject_id, owner_staff_id, provider_id, branch_id, question_type) "
                   f"select gen_random_uuid(), 'GLOBAL', '{SUBJECT}', gen_random_uuid(), null, null, '{qtype}' returning question_id")[0][0]


def version(db, qid, qtype: str, content: str | None, key: str | None, correct: str | None = None, options: str = "''",
            negative: str = "null", positive: str = "null", schema: str = "1") -> None:
    c = f"'{content}'" if content else "null"
    k = f"'{key}'" if key else "null"
    letter = f"'{correct}'" if correct else "null"
    sql(db, f"insert into question_versions(question_version_id, question_id, version_no, stem, option_a, option_b, option_c, option_d, "
            f"correct_option, question_type, content, answer_key, schema_version, marks_positive, marks_negative, created_by) values "
            f"(gen_random_uuid(), '{qid}', (select coalesce(max(version_no), 0) + 1 from question_versions where question_id = '{qid}'), "
            f"'Q', {options}, {options}, {options}, {options}, {letter}, '{qtype}', {c}, {k}, {schema}, {positive}, {negative}, gen_random_uuid())")


def test_existing_mcq_versions_upgrade_unchanged_and_the_three_shapes_are_enforced(db):
    at_0014_with_data(db)
    before = sql(db, f"select stem, option_a, correct_option, marks_positive from question_versions where question_id = '{QUESTION}'")
    run(db, "paper", "upgrade", "head")
    assert head(db) == PAPER_HEAD == "0018_question_bank_search"
    assert sql(db, f"select stem, option_a, correct_option, marks_positive, question_type, content from question_versions "
                   f"where question_id = '{QUESTION}'") == [(*before[0], "MCQ_SINGLE", None)]

    msq, nat = typed_question(db, "MCQ_MULTI"), typed_question(db, "NUMERICAL")
    version(db, msq, "MCQ_MULTI", MSQ_CONTENT, json.dumps({"correct": ["A"]}), positive="4", negative="0")
    version(db, nat, "NUMERICAL", json.dumps({"format": "DECIMAL", "decimals": 2, "unit": None}),
            json.dumps({"value": "1.5", "tolerance": {"kind": "EXACT"}}), positive="4", negative="1")
    bad = [  # each breaks the shape of its type
        lambda: version(db, msq, "MCQ_MULTI", MSQ_CONTENT, None),                                       # no key
        lambda: version(db, msq, "MCQ_MULTI", MSQ_CONTENT, json.dumps({"correct": ["A"]}), correct="A"),  # a letter too
        lambda: version(db, msq, "MCQ_MULTI", MSQ_CONTENT, json.dumps({"correct": ["A"]}), options="'x'"),  # option columns used
        lambda: version(db, msq, "MCQ_MULTI", MSQ_CONTENT, json.dumps({"correct": ["A"]}), positive="4", negative="1"),  # MSQ negative
        lambda: version(db, msq, "MCQ_MULTI", MSQ_CONTENT, json.dumps({"correct": ["A"]}), schema="null"),
        lambda: version(db, QUESTION, "MCQ_SINGLE", MSQ_CONTENT, None, correct="A"),                    # MCQ with content
        lambda: version(db, QUESTION, "MCQ_SINGLE", None, None, correct=None, options="'a'"),           # MCQ without a letter
        lambda: version(db, msq, "ESSAY", MSQ_CONTENT, json.dumps({"correct": ["A"]})),                 # unknown type
    ]
    for make in bad:
        with pytest.raises(IntegrityError):
            make()
    with pytest.raises((InternalError, ProgrammingError, IntegrityError)):
        sql(db, f"update question_versions set answer_key = '{{}}' where question_id = '{nat}'")       # versions stay append-only
    asset = sql(db, "insert into question_image_assets(asset_id, provider_id, file_name, mime_type, file_size, width, height, checksum, "
                    "storage, storage_key, created_by) values (gen_random_uuid(), null, 'a.png', 'image/png', 1, 1, 1, "
                    f"'{'e' * 64}', 'local', 'question-images/platform/{'e' * 64}', gen_random_uuid()) returning asset_id")[0][0]
    qv = sql(db, f"select question_version_id from question_versions where question_id = '{msq}'")[0][0]
    sql(db, f"insert into question_version_assets(question_version_id, role, asset_id) values ('{qv}', 'option_a', '{asset}')")
    with pytest.raises((InternalError, ProgrammingError, IntegrityError)):
        sql(db, f"delete from question_version_assets where question_version_id = '{qv}'")             # links are append-only too


def test_a_new_database_adopts_the_baseline_shapes(db):
    paper_at(db, "head")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    assert head(db) == PAPER_HEAD
    checks = {r[0] for r in sql(db, "select conname from pg_constraint where conname in ('question_versions_shape_check', "
                                    "'question_versions_msq_negative_check', 'question_versions_type_check')")}
    assert len(checks) == 3
    assert sql(db, "select count(*) from pg_trigger where tgname = 'question_version_assets_immutable'")[0][0] == 1
    run(db, "paper", "downgrade", BEFORE)                # empty: the old shapes come back
    run(db, "paper", "upgrade", "head")


def test_downgrade_refuses_while_msq_or_nat_exists(db):
    at_0014_with_data(db)
    run(db, "paper", "upgrade", "head")
    nat = typed_question(db, "NUMERICAL")
    refused = alembic(db, "paper", "downgrade", BEFORE)
    assert refused.returncode != 0 and "Downgrade refused" in refused.stderr and head(db) == PAPER_HEAD
    sql(db, f"delete from bank_questions where question_id = '{nat}'")
    run(db, "paper", "downgrade", BEFORE)
    assert head(db) == BEFORE
    assert sql(db, f"select correct_option from question_versions where question_id = '{QUESTION}'") == [("A",)]
    run(db, "paper", "upgrade", "head")
