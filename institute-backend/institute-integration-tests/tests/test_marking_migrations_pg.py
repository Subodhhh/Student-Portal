"""Paper 0010_optional_question_marks and exam 0013_marking_rules on REAL PostgreSQL (each test in its own throwaway database on the
TEST_DATABASE_URL server):

  * existing whole-number marks, paper totals and UNIFORM plans survive the upgrade unchanged;
  * after the upgrade the database itself enforces "both marks or neither" and "RULES <=> marking_rules";
  * a downgrade refuses while data that only the new revisions can represent exists, and works (and re-upgrades) once it is gone.
Skipped on the default SQLite run (needs TEST_DATABASE_URL, PostgreSQL)."""
import pytest
from sqlalchemy.exc import IntegrityError

from tests.test_paper_migration_0006_pg import AFTER_PAPER, alembic, db, paper_at, pytestmark, run, sql  # noqa: F401  (db: fixture; pytestmark: PostgreSQL only)

PROVIDER, BRANCH, SUBJECT = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"
QUESTION, PAPER = "44444444-4444-4444-4444-444444444444", "66666666-6666-6666-6666-666666666666"
RULES = '{"format": 1, "rules": [{"subject_id": null, "question_type": "MCQ_SINGLE", "correct": "1.25", "wrong": "0.25"}]}'


def at_previous_revisions(db) -> None:
    """Every chain at head except paper (0009) and exam (0012): the schema just before Phase A."""
    paper_at(db, "0009_picture_recognition_jobs")
    for s in AFTER_PAPER:
        run(db, s, "upgrade", "head")
    run(db, "exam", "downgrade", "0012_exam_branch_indexes")


def seed_whole_number_data(db) -> None:
    status = lambda table: sql(db, f"select (enum_range(null::{udt(db, table)}))[1]")[0][0]  # noqa: E731
    sql(db, f"insert into providers(provider_id, name, email, status, created_at, updated_at) "
            f"values ('{PROVIDER}', 'P', 'p@example.test', '{status('providers')}', now(), now())")
    sql(db, f"insert into branches(branch_id, provider_id, name, status, created_at, updated_at) "
            f"values ('{BRANCH}', '{PROVIDER}', 'B', '{status('branches')}', now(), now())")
    sql(db, f"insert into subjects(subject_id, code, name) values ('{SUBJECT}', 'PHY', 'Physics')")
    sql(db, f"insert into bank_questions(question_id, subject_id, owner_staff_id, provider_id, branch_id) "
            f"values ('{QUESTION}', '{SUBJECT}', gen_random_uuid(), '{PROVIDER}', '{BRANCH}')")
    sql(db, version_sql(1, "4", "1"))
    sql(db, f"insert into papers(paper_id, subject_id, title, created_by, provider_id, branch_id) "
            f"values ('{PAPER}', '{SUBJECT}', 'T', gen_random_uuid(), '{PROVIDER}', '{BRANCH}')")
    sql(db, f"insert into paper_versions(paper_version_id, paper_id, version_no, question_count, total_marks, content_hash, created_by) "
            f"values (gen_random_uuid(), '{PAPER}', 1, 1, 4, 'h', gen_random_uuid())")
    sql(db, f"insert into exam_plans(plan_id, provider_id, branch_id, name, duration_minutes, start_at, end_at, created_by, marking_mode, "
            f"marks_correct, marks_wrong) values (gen_random_uuid(), '{PROVIDER}', '{BRANCH}', 'U', 60, now(), now() + interval '1 hour', "
            f"gen_random_uuid(), 'UNIFORM', 4, 1)")


def udt(db, table: str) -> str:
    return sql(db, f"select udt_name from information_schema.columns where table_name = '{table}' and column_name = 'status'")[0][0]


def version_sql(version_no: int, positive: str, negative: str) -> str:
    return (f"insert into question_versions(question_version_id, question_id, version_no, stem, option_a, option_b, option_c, option_d, "
            f"correct_option, marks_positive, marks_negative, created_by) values (gen_random_uuid(), '{QUESTION}', {version_no}, 'Q', "
            f"'a', 'b', 'c', 'd', 'A', {positive}, {negative}, gen_random_uuid())")


def upgrade(db) -> None:
    run(db, "paper", "upgrade", "head")
    run(db, "exam", "upgrade", "head")


def test_existing_marks_survive_and_the_database_enforces_the_new_rules(db):
    at_previous_revisions(db)
    seed_whole_number_data(db)
    upgrade(db)
    assert sql(db, "select marks_positive::text, marks_negative::text from question_versions") == [("4.00", "1.00")]
    assert sql(db, "select total_marks::text from paper_versions") == [("4.00",)]
    assert sql(db, "select marking_mode, marks_correct, marks_wrong, marking_rules is null from exam_plans") == [("UNIFORM", 4, 1, True)]

    sql(db, version_sql(2, "null", "null"))                                     # no marks of its own: allowed
    sql(db, version_sql(3, "1.25", "0.25"))                                     # decimal marks
    for positive, negative in (("null", "1"), ("4", "null"), ("0", "0"), ("4", "-1")):
        with pytest.raises(IntegrityError):                                     # one mark without the other, or out of range
            sql(db, version_sql(9, positive, negative))
    with pytest.raises(IntegrityError):                                         # RULES without rules
        sql(db, "update exam_plans set marking_mode = 'RULES', marks_correct = null, marks_wrong = null")
    with pytest.raises(IntegrityError):                                         # rules on another mode
        sql(db, f"update exam_plans set marking_rules = '{RULES}'")
    sql(db, f"update exam_plans set marking_mode = 'RULES', marks_correct = null, marks_wrong = null, marking_rules = '{RULES}'")


def test_downgrade_refuses_new_data_then_round_trips_once_it_is_gone(db):
    at_previous_revisions(db)
    seed_whole_number_data(db)
    upgrade(db)
    sql(db, f"update exam_plans set marking_mode = 'RULES', marks_correct = null, marks_wrong = null, marking_rules = '{RULES}'")
    refused = alembic(db, "exam", "downgrade", "0012_exam_branch_indexes")
    assert refused.returncode != 0 and "RULES marking" in refused.stderr
    sql(db, "update exam_plans set marking_mode = 'UNIFORM', marks_correct = 4, marks_wrong = 1, marking_rules = null")
    run(db, "exam", "downgrade", "0012_exam_branch_indexes")

    sql(db, "insert into paper_versions(paper_version_id, paper_id, version_no, question_count, total_marks, content_hash, created_by) "
            f"values (gen_random_uuid(), '{PAPER}', 2, 1, null, 'h2', gen_random_uuid())")
    refused = alembic(db, "paper", "downgrade", "0009_picture_recognition_jobs")
    assert refused.returncode != 0 and "have no marks" in refused.stderr
    sql(db, "delete from paper_versions where total_marks is null")
    run(db, "paper", "downgrade", "0009_picture_recognition_jobs")
    assert sql(db, "select is_nullable from information_schema.columns where table_name = 'question_versions' "
                   "and column_name = 'marks_positive'") == [("NO",)]
    upgrade(db)
    assert sql(db, "select marks_positive::text from question_versions") == [("4.00",)]
