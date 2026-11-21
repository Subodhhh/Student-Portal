"""Rankings with decimal marks, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_decimal_ranking_pg.py

Marks of 1.25 / 0.25 and a fractional subject adjustment: scores are exact, ordering follows the exact percentage, and equal
fractions (0.1 of 0.3 and 0.2 of 0.6) share a rank. Rows are unique per run; nothing existing is touched."""
import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.test_occurrence_ranking_pg import URL, pytestmark  # noqa: F401  (same disposable-database guard)

SECTIONS = [{"position": 1, "subject": "Physics", "status": "PAPER", "message": None, "question_count": 2}]


@pytest.fixture()
def world():
    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)
    run = uuid.uuid4().hex[:6].upper()
    p, b, occurrence = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sid = {k: f"D{run}{k}" for k in "XYZWV"}
    with engine.begin() as c:
        for k, s in sid.items():
            c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                           "VALUES (:s, :f, 'Dec', :e, 'ACTIVE', now(), now())"), {"s": s, "f": f"Student{k}", "e": f"{s.lower()}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})

    def take(student, marks, answers, obtained, adjustments=None):
        """A Physics test of questions worth `marks` (negative marks 0.25) answered with `answers` ('A' is right, None blank)."""
        tid, aid = uuid.uuid4(), uuid.uuid4()
        total = sum(marks)
        with engine.begin() as c:
            c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                              status, global_occurrence_id, sections, created_at, updated_at) VALUES (:t, :p, :b, 'Dec', 60, 2, :m, now() - interval '2 hours',
                              now() + interval '2 hours', 'ACTIVE', :o, CAST(:s AS jsonb), now(), now())"""),
                      {"t": tid, "p": p, "b": b, "m": total, "o": occurrence, "s": json.dumps(SECTIONS)})
            c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status)
                              VALUES (:a, :t, :s, now() - interval '1 hour', now() + interval '1 hour', now(), 'SUBMITTED')"""), {"a": aid, "t": tid, "s": student})
            for order, (m, ans) in enumerate(zip(marks, answers), start=1):
                q = uuid.uuid4()
                c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, question_text, question_type, option_a, option_b,
                                  option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                                  VALUES (:q, :t, :o, 'Physics', 'Q', 'MCQ', 'a', 'b', 'c', 'd', 'A', :m, 0.25, now(), now())"""),
                          {"q": q, "t": tid, "o": order, "m": m})
                if ans:
                    c.execute(text("""INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, answered_at, updated_at, question_state)
                                      VALUES (:i, :a, :q, :x, now(), now(), 'ANSWERED')"""), {"i": uuid.uuid4(), "a": aid, "q": q, "x": ans})
            c.execute(text("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions, correct_answers,
                              incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, subject_adjustments, created_at)
                              VALUES (:r, :a, :t, :s, 2, 0, 0, 0, 0, :m, :o, 0, CAST(:adj AS jsonb), now())"""),
                      {"r": uuid.uuid4(), "a": aid, "t": tid, "s": student, "m": total, "o": obtained, "adj": json.dumps(adjustments) if adjustments else None})
        return tid

    t = {
        "X": take(sid["X"], [1.25, 1.25], ["A", "A"], 2.5),                        # 2.5 / 2.5 = 100%
        "Y": take(sid["Y"], [1.25, 1.25], ["A", "B"], 1.0),                        # 1.25 - 0.25 = 1 / 2.5 = 40%
        "Z": take(sid["Z"], [1.25, 1.25], ["A", None], 1.5, {"Physics": 0.25}),    # 1.25 + 0.25 adjustment = 1.5 / 2.5 = 60%
        "W": take(sid["W"], [0.1, 0.2], ["A", None], 0.1),                         # 0.1 / 0.3 = 1/3
        "V": take(sid["V"], [0.2, 0.4], ["A", None], 0.2),                         # 0.2 / 0.6 = 1/3: a tie with W, exactly
    }
    yield Session, sid, t
    engine.dispose()


def ranking(Session, test_id, student_id):
    from app.services.ranking_service import get_ranking
    with Session() as db:
        return get_ranking(db=db, test_id=test_id, student_id=student_id)


def test_overall_and_subject_rankings_are_exact_and_ties_share_a_rank(world):
    from decimal import Decimal as D
    Session, sid, t = world
    r = ranking(Session, t["Y"], sid["Y"])
    rows = [(e["student_name"], e["rank"], e["obtained_marks"], e["max_marks"], e["percentage"]) for e in r["leaderboard"]]
    assert rows == [("StudentX Dec", 1, D("2.5"), D("2.5"), 100.0), ("StudentZ Dec", 2, D("1.5"), D("2.5"), 60.0),
                    ("StudentY Dec", 3, D("1"), D("2.5"), 40.0),
                    ("StudentV Dec", 4, D("0.2"), D("0.6"), 33.33), ("StudentW Dec", 4, D("0.1"), D("0.3"), 33.33)]
    assert (r["my_rank"], r["my_score"], r["my_max_marks"]) == (3, D("1"), D("2.5"))
    physics = r["subjects"][0]
    # the subject view is scored from the answers with the fractional adjustment added: Z = 1.25 + 0.25
    got = {e["student_name"]: (e["rank"], e["obtained_marks"]) for e in physics["leaderboard"]}
    assert got["StudentZ Dec"] == (2, D("1.50")) and got["StudentY Dec"] == (3, D("1.00"))
    assert got["StudentV Dec"][0] == got["StudentW Dec"][0] == 4


def test_the_route_serves_whole_marks_as_integers_and_fractions_as_numbers(world):
    from app.auth import get_current_student
    from app.database import get_db
    from app.main import app
    Session, sid, t = world

    def db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_student] = lambda: sid["Y"]
    try:
        with TestClient(app) as c:
            text_body = c.get(f"/ranking/{t['Y']}").text
    finally:
        app.dependency_overrides.clear()
    assert '"my_score":1,' in text_body and '"obtained_marks":1.5,' in text_body and '"max_marks":2.5,' in text_body
    assert not re.search(r'"(obtained_marks|max_marks|my_score|my_max_marks)":\d+\.0[,}]', text_body)    # never 4.0
