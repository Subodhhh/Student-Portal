"""Ranking with ties and with MSQ / NAT questions, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5439/stu_qt_verify pytest tests/test_question_types_ranking_pg.py

A test's ranking is standard competition ranking (1, 2, 2, 4; decision R3), listed by name inside a shared rank, never by who was
evaluated first; a Global Test's subject ranking takes MSQ / NAT marks from the result service's stored per-question marks (the one
scorer), not from a letter comparison."""
import json
import uuid
from decimal import Decimal as D

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.test_decimal_ranking_pg import SECTIONS, URL, pytestmark  # noqa: F401  (same disposable-PostgreSQL guard)


def setup(engine, run, names):
    p, b = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        for s, name in names.items():
            c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                           "VALUES (:s, :f, 'Tie', :e, 'ACTIVE', now(), now())"), {"s": s, "f": name, "e": f"{s.lower()}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})
    return p, b


def test_equal_marks_share_a_rank_and_are_listed_by_name(monkeypatch):
    from app.services.ranking_service import get_ranking
    engine = create_engine(URL)
    run = uuid.uuid4().hex[:6].upper()
    names = {f"R{run}1": "Zara", f"R{run}2": "Amit", f"R{run}3": "Bela", f"R{run}4": "Chen"}
    p, b = setup(engine, run, names)
    tid = uuid.uuid4()
    marks = {f"R{run}1": 10, f"R{run}2": 8, f"R{run}3": 8, f"R{run}4": 5}
    with engine.begin() as c:
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, created_at, updated_at) VALUES (:t, :p, :b, 'Ties', 60, 3, 12, now() - interval '2 hours', now() - interval '1 hour',
                          'ACTIVE', now(), now())"""), {"t": tid, "p": p, "b": b})
        # Bela is evaluated BEFORE Amit: the old tie-break (evaluation time) would have put her first
        for s in (f"R{run}1", f"R{run}3", f"R{run}2", f"R{run}4"):
            aid = uuid.uuid4()
            c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status)
                              VALUES (:a, :t, :s, now() - interval '1 hour', now(), now(), 'SUBMITTED')"""), {"a": aid, "t": tid, "s": s})
            c.execute(text("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions, correct_answers,
                              incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, created_at)
                              VALUES (:r, :a, :t, :s, 3, 3, 0, 0, 0, 12, :o, 0, clock_timestamp())"""),
                      {"r": uuid.uuid4(), "a": aid, "t": tid, "s": s, "o": marks[s]})
    with sessionmaker(bind=engine)() as db:
        r = get_ranking(db=db, test_id=tid, student_id=f"R{run}3")
    assert [(e["student_name"], e["rank"]) for e in r["leaderboard"]] == [("Zara Tie", 1), ("Amit Tie", 2), ("Bela Tie", 2), ("Chen Tie", 4)]
    assert r["my_rank"] == 2
    engine.dispose()


def test_global_subject_ranking_uses_the_stored_marks_of_msq_and_nat():
    from app.services.ranking_service import get_ranking
    engine = create_engine(URL)
    run = uuid.uuid4().hex[:6].upper()
    names = {f"G{run}1": "Asha", f"G{run}2": "Bo"}
    p, b = setup(engine, run, names)
    occurrence = uuid.uuid4()
    tests = {}
    with engine.begin() as c:
        for s, (answer, awarded, status) in zip(names, (("AC", 4, "CORRECT"), ("A", 0, "INCORRECT"))):
            tid, aid, rid, q = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            tests[s] = tid
            c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                              status, global_occurrence_id, sections, created_at, updated_at) VALUES (:t, :p, :b, 'G', 60, 1, 4, now() - interval '2 hours',
                              now() + interval '2 hours', 'ACTIVE', :o, CAST(:sec AS jsonb), now(), now())"""),
                      {"t": tid, "p": p, "b": b, "o": occurrence, "sec": json.dumps(SECTIONS)})
            c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, question_text, question_type, content, answer_key, scheme,
                              correct_answer, marks, negative_marks, created_at, updated_at) VALUES (:q, :t, 1, 'Physics', 'Q', 'MCQ_MULTI',
                              CAST(:c AS jsonb), CAST(:k AS jsonb), 'ALL_OR_NOTHING', '', 4, 0, now(), now())"""),
                      {"q": q, "t": tid, "c": json.dumps({"options": [{"id": x, "text": x, "image": None} for x in "ABCD"]}),
                       "k": json.dumps({"correct": ["A", "C"]})})
            c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status)
                              VALUES (:a, :t, :s, now() - interval '1 hour', now() + interval '1 hour', now(), 'SUBMITTED')"""), {"a": aid, "t": tid, "s": s})
            c.execute(text("""INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, answered_at, updated_at, question_state)
                              VALUES (:i, :a, :q, :x, now(), now(), 'ANSWERED')"""), {"i": uuid.uuid4(), "a": aid, "q": q, "x": answer})
            c.execute(text("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions, correct_answers,
                              incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, created_at)
                              VALUES (:r, :a, :t, :s, 1, 1, 0, 0, 0, 4, :o, 0, now())"""), {"r": rid, "a": aid, "t": tid, "s": s, "o": awarded})
            c.execute(text("""INSERT INTO test_result_questions (result_id, question_id, question_order, question_type, selected_answer, status,
                              marks_obtained) VALUES (:r, :q, 1, 'MCQ_MULTI', :x, :st, :m)"""), {"r": rid, "q": q, "x": answer, "st": status, "m": awarded})
    with sessionmaker(bind=engine)() as db:
        r = get_ranking(db=db, test_id=tests[f"G{run}1"], student_id=f"G{run}1")
    physics = {e["student_name"]: (e["rank"], e["obtained_marks"]) for e in r["subjects"][0]["leaderboard"]}
    assert physics == {"Asha Tie": (1, D("4")), "Bo Tie": (2, D("0"))}       # a letter comparison would have given Asha 0
    engine.dispose()
