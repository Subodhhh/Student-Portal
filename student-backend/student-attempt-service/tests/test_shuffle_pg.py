"""Shuffle Questions (tests.shuffle_questions) in the attempt service, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_shuffle_pg.py

Each student gets an order of their own that never changes for them (reloads, resume), subjects keep their place, the questions
themselves are untouched and answers stay on their question; without the option the existing order is kept."""
from sqlalchemy import text

from tests.test_sections_pg import env, pytestmark  # noqa: F401  (same disposable-database fixture and guard)


def _shuffled(engine, tid):
    with engine.begin() as c:
        c.execute(text("UPDATE tests SET shuffle_questions = true WHERE test_id = :t"), {"t": tid})


def test_each_student_gets_a_stable_order_of_their_own_with_subjects_in_place(env):  # noqa: F811
    clients, engine, make_test, _ = env
    tid, qids = make_test({"Chemistry": 6, "Physics": 6})
    _shuffled(engine, tid)
    with engine.connect() as c:
        stored = {str(r.question_id): (r.question_text, r.option_a, r.option_b, r.option_c, r.option_d, r.marks, r.negative_marks)
                  for r in c.execute(text("SELECT * FROM questions WHERE test_id = :t"), {"t": tid})}
    orders = []
    for c in clients:
        attempt = c.post("/attempts/start", json={"test_id": str(tid)}).json()
        first = c.get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]
        assert [q["subject"] for q in first] == ["Chemistry"] * 6 + ["Physics"] * 6          # each subject keeps its place
        assert sorted(q["question_id"] for q in first) == sorted(map(str, qids))              # the same questions, each once
        for q in first:                                                                       # content and marks untouched
            assert (q["question_text"], q["option_a"], q["option_b"], q["option_c"], q["option_d"], q["marks"], q["negative_marks"]) \
                == stored[q["question_id"]]
        again = c.get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]
        assert [q["question_id"] for q in again] == [q["question_id"] for q in first]          # a refresh never reshuffles
        resumed = c.post("/attempts/start", json={"test_id": str(tid)}).json()
        assert resumed["attempt_id"] == attempt["attempt_id"]
        assert [q["question_id"] for q in c.get(f"/attempts/{resumed['attempt_id']}/workspace").json()["questions"]] \
            == [q["question_id"] for q in first]                                               # nor does resuming
        orders.append([q["question_id"] for q in first])
    paper = [str(q) for q in qids]                                                            # make_test inserts in paper order
    assert any(o != paper for o in orders)                                                    # actually shuffled
    assert len({tuple(o) for o in orders}) > 1                                                # students do not share one order
    # an answer stays on its question through a reload
    c = clients[1]
    attempt = c.post("/attempts/start", json={"test_id": str(tid)}).json()
    target = orders[1][2]
    assert c.post(f"/attempts/{attempt['attempt_id']}/answers", json={"question_id": target, "selected_answer": "C"}).status_code == 200
    after = c.get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]
    assert [q["question_id"] for q in after] == orders[1]
    assert {q["question_id"]: q["selected_answer"] for q in after if q["selected_answer"]} == {target: "C"}


def test_without_shuffle_the_existing_order_is_kept(env):  # noqa: F811
    clients, engine, make_test, _ = env
    tid, _ = make_test({"Physics": 3, "Chemistry": 3})
    with engine.connect() as c:
        assert c.execute(text("SELECT shuffle_questions FROM tests WHERE test_id = :t"), {"t": tid}).scalar() is False   # the default
    for c in clients:
        attempt = c.post("/attempts/start", json={"test_id": str(tid)}).json()
        qs = c.get(f"/attempts/{attempt['attempt_id']}/workspace").json()["questions"]
        assert [(q["subject"], q["question_order"]) for q in qs] == [("Chemistry", 4), ("Chemistry", 5), ("Chemistry", 6),
                                                                     ("Physics", 1), ("Physics", 2), ("Physics", 3)]
