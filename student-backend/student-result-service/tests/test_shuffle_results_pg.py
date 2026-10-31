"""Shuffle Questions in the result service, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_shuffle_results_pg.py

The order never changes a score (answers are evaluated by question, with the same marks and negative marks), and the answer review lists
the questions in the order this student sat them."""
from app.services.question_order import section_order
from sqlalchemy import text

from tests.test_auto_evaluation_pg import env, pytestmark  # noqa: F401  (same disposable-database fixture and guard)


def test_a_shuffled_test_scores_the_same_and_reviews_in_the_students_order(env):  # noqa: F811
    client, engine, aid, tid, auto_evaluation = env
    with engine.begin() as c:
        c.execute(text("UPDATE tests SET shuffle_questions = true WHERE test_id = :t"), {"t": tid})
    auto_evaluation.evaluate_pending()
    r = client.get(f"/results/attempts/{aid}").json()
    # one right (+4), one wrong (-1), one unanswered (0): exactly as without shuffling
    assert (r["obtained_marks"], r["total_marks"], r["correct_answers"], r["incorrect_answers"], r["unanswered_questions"]) == (3, 12, 1, 1, 1)
    with engine.begin() as c:
        c.execute(text("UPDATE tests SET end_at = now() - interval '1 second' WHERE test_id = :t"), {"t": tid})
        sid = c.execute(text("SELECT student_id FROM test_attempts WHERE attempt_id = :a"), {"a": aid}).scalar()
        paper = [{"question_id": str(q), "subject": "Physics"} for (q,) in
                 c.execute(text("SELECT question_id FROM questions WHERE test_id = :t ORDER BY question_order"), {"t": tid})]
    d = client.get(f"/results/attempts/{aid}/details").json()
    expected = [x["question_id"] for x in section_order(paper, None, sid, tid, subject_of=lambda x: x["subject"],
                                                        id_of=lambda x: x["question_id"], shuffle=True)]
    assert [str(x["question_id"]) for x in d["questions"]] == expected
    by_order = {x["question_order"]: x["status"] for x in d["questions"]}
    assert by_order == {1: "CORRECT", 2: "INCORRECT", 3: "UNANSWERED"}                    # each status stays on its question
