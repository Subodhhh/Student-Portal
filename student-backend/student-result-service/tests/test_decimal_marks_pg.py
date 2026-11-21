"""Decimal marks through the real evaluation SQL, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_decimal_marks_pg.py

Questions of 1.25 (wrong: -0.25) are scored exactly and served as JSON numbers; the whole-number test next to it still serves integers."""
from sqlalchemy import text

from tests.test_auto_evaluation_pg import env, pytestmark  # noqa: F401  (same disposable-database fixture and guard)


def test_decimal_marks_are_scored_exactly_and_served_as_json_numbers(env):
    client, engine, aid, tid, auto_evaluation = env
    with engine.begin() as c:       # the fixture's attempt: Q1 right, Q2 wrong, Q3 unanswered
        c.execute(text("UPDATE questions SET marks = 1.25, negative_marks = 0.25 WHERE test_id = :t"), {"t": tid})
        c.execute(text("UPDATE tests SET total_marks = 3.75 WHERE test_id = :t"), {"t": tid})
        c.execute(text("UPDATE tests SET end_at = now() - interval '1 second' WHERE test_id = :t"), {"t": tid})
    auto_evaluation.evaluate_pending()
    body = client.get(f"/results/attempts/{aid}")
    assert '"obtained_marks":1,' in body.text and '"total_marks":3.75,' in body.text       # 1.25 - 0.25 = 1 exactly: an integer on the wire
    r = body.json()
    assert (r["obtained_marks"], r["total_marks"]) == (1, 3.75)
    d = client.get(f"/results/attempts/{aid}/details").json()
    assert sorted(q["marks_obtained"] for q in d["questions"]) == [-0.25, 0, 1.25]
    assert d["subjects"][0]["obtained_marks"] == 1 and d["subjects"][0]["total_marks"] == 3.75
    with engine.connect() as c:
        assert str(c.execute(text("SELECT obtained_marks FROM test_results WHERE attempt_id = :a"), {"a": aid}).scalar()) == "1.00"
