"""Global Test rankings per OCCURRENCE (overall + per configured subject), on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55433/stu_unified_verify pytest tests/test_occurrence_ranking_pg.py

Each student of a unified Global Test occurrence has a Student test of their own (their papers); the ranking spans every test of the
occurrence, by percentage of the marks actually delivered to each student. Rows are unique per run; nothing existing is touched."""
import json
import os
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")

SECTIONS = [{"position": 1, "subject": "Biology", "status": "PAPER", "message": None, "question_count": 2},
            {"position": 2, "subject": "Physics", "status": "PAPER", "message": None, "question_count": 2},
            {"position": 3, "subject": "Chemistry", "status": "PAPER", "message": None, "question_count": 2}]


@pytest.fixture()
def world():
    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)
    run = uuid.uuid4().hex[:6].upper()
    p, b = uuid.uuid4(), uuid.uuid4()
    sid = {k: f"R{run}{k}" for k in "ABCDEFG"}
    with engine.begin() as c:
        for k, s in sid.items():
            c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                           "VALUES (:s, :f, 'Rank', :e, 'ACTIVE', now(), now())"), {"s": s, "f": f"Student{k}", "e": f"{s.lower()}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": p, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": b, "p": p})

    def take(student, occurrence, papers: dict[str, list[str | None]], *, sections=SECTIONS, submit=True, name="NEET UG - 02(testing)"):
        """A Student test with `papers` = {subject: [answer per question]} (correct answer is always 'A'; None = unanswered), an attempt
        with those answers and, when submitted, a result scored with the result service's rules (+4 right, -1 wrong, 0 blank)."""
        tid, aid = uuid.uuid4(), uuid.uuid4()
        n = sum(len(v) for v in papers.values())
        obtained = sum(4 if a == "A" else -1 if a else 0 for v in papers.values() for a in v)
        with engine.begin() as c:
            c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                              status, global_occurrence_id, sections, created_at, updated_at) VALUES (:t, :p, :b, :n, 60, :q, :m, now() - interval '2 hours',
                              now() + interval '2 hours', 'ACTIVE', :o, CAST(:s AS jsonb), now(), now())"""),
                      {"t": tid, "p": p, "b": b, "n": name, "q": n, "m": 4 * n, "o": occurrence, "s": json.dumps(sections) if occurrence else None})
            c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status)
                              VALUES (:a, :t, :s, now() - interval '1 hour', now() + interval '1 hour', :sub, :st)"""),
                      {"a": aid, "t": tid, "s": student, "sub": "now" if submit else None, "st": "SUBMITTED" if submit else "IN_PROGRESS"})
            order = 0
            for subject, answers in papers.items():
                for ans in answers:
                    order += 1
                    q = uuid.uuid4()
                    c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, question_text, question_type, option_a,
                                      option_b, option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                                      VALUES (:q, :t, :o, :s, 'secret stem', 'MCQ', 'a', 'b', 'c', 'd', 'A', 4, 1, now(), now())"""),
                              {"q": q, "t": tid, "o": order, "s": subject})
                    if ans:
                        c.execute(text("""INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, answered_at, updated_at, question_state)
                                          VALUES (:i, :a, :q, :x, now(), now(), 'ANSWERED')"""), {"i": uuid.uuid4(), "a": aid, "q": q, "x": ans})
            if submit:
                c.execute(text("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions, correct_answers,
                                  incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, created_at)
                                  VALUES (:r, :a, :t, :s, :n, 0, 0, 0, 0, :m, :o, 0, now())"""),
                          {"r": uuid.uuid4(), "a": aid, "t": tid, "s": student, "n": n, "m": 4 * n, "o": obtained})
        return tid

    o1, o2 = uuid.uuid4(), uuid.uuid4()
    t = {
        "A": take(sid["A"], o1, {"Physics": ["A", "A"], "Chemistry": ["A", "A"]}),          # 16/16 = 100%
        "B": take(sid["B"], o1, {"Physics": ["A", "A"], "Chemistry": ["B", None]}),         # 8 - 1 = 7/16 = 43.75%
        "C": take(sid["C"], o1, {"Biology": ["A", "A"]},                                    # 8/8 = 100% (only Biology delivered)
                  sections=[dict(SECTIONS[0]), dict(SECTIONS[1], status="NOT_ENROLLED", question_count=0),
                            dict(SECTIONS[2], status="NOT_COVERED", question_count=0)]),
        "D": take(sid["D"], o1, {"Physics": ["A", None]}),                                  # 4/8 = 50%
        "E": take(sid["E"], o1, {"Physics": ["A", "A"]}, submit=False),                     # never submitted: not ranked
        "F": take(sid["F"], o2, {"Physics": ["A", "A"]}),                                   # same name, ANOTHER occurrence
        "G": take(sid["G"], None, {"Physics": ["A", "B"]}, name="Daily Test"),             # an ordinary (Daily) test
    }
    yield Session, sid, t
    engine.dispose()


def ranking(Session, test_id, student_id):
    from app.services.ranking_service import get_ranking
    with Session() as db:
        return get_ranking(db=db, test_id=test_id, student_id=student_id)


def view(board):
    return [(e["student_name"], e["rank"], e["obtained_marks"], e["max_marks"], e["percentage"]) for e in board["leaderboard"]]


def test_one_overall_ranking_for_the_whole_occurrence_by_percentage_with_shared_ranks(world):
    Session, sid, t = world
    r = ranking(Session, t["B"], sid["B"])
    assert r["total_students"] == 4                                                  # E never submitted; F is another occurrence
    assert view(r) == [("StudentA Rank", 1, 16, 16, 100.0), ("StudentC Rank", 1, 8, 8, 100.0),   # equal %: shared rank, raw marks order
                       ("StudentD Rank", 3, 4, 8, 50.0), ("StudentB Rank", 4, 7, 16, 43.75)]
    assert (r["my_rank"], r["my_score"], r["my_max_marks"], r["my_percentage"]) == (4, 7, 16, 43.75)
    for k in "ACD":                                                                  # every participant sees the same ranking
        other = ranking(Session, t[k], sid[k])
        assert view(other) == view(r) and other["total_students"] == 4 and other["occurrence_id"] == r["occurrence_id"]


def test_subject_rankings_in_configured_order_with_only_students_who_received_the_subject(world):
    Session, sid, t = world
    r = ranking(Session, t["A"], sid["A"])
    assert [s["subject"] for s in r["subjects"]] == ["Biology", "Physics", "Chemistry"]
    bio, phy, che = r["subjects"]
    assert (bio["total_students"], view(bio)) == (1, [("StudentC Rank", 1, 8, 8, 100.0)])          # a missing subject is never 0%
    assert bio["my_rank"] is None                                                                  # A had no Biology paper
    assert view(phy) == [("StudentA Rank", 1, 8, 8, 100.0), ("StudentB Rank", 1, 8, 8, 100.0), ("StudentD Rank", 3, 4, 8, 50.0)]
    assert view(che) == [("StudentA Rank", 1, 8, 8, 100.0), ("StudentB Rank", 2, -1, 8, -12.5)]    # one wrong, one blank; C: NOT_COVERED, absent
    assert (phy["my_rank"], che["my_rank"]) == (1, 1)


def test_only_participants_of_the_occurrence_see_it_and_nothing_about_papers_leaks(world):
    from fastapi import HTTPException
    Session, sid, t = world
    for outsider in ("E", "F", "G"):                       # unsubmitted, another occurrence with the same name, a Daily-test student
        with pytest.raises(HTTPException) as e:
            ranking(Session, t["A"], sid[outsider])
        assert e.value.status_code == 404
    f = ranking(Session, t["F"], sid["F"])
    assert [x["student_id"] for x in f["leaderboard"]] == [sid["F"]] and f["total_students"] == 1   # occurrences never mixed
    body = json.dumps(ranking(Session, t["A"], sid["A"]), default=str)
    assert not re.search(r"secret stem|correct_answer|selected_answer|provider", body)


def test_daily_and_weekly_rankings_keep_their_behaviour(world):
    Session, sid, t = world
    r = ranking(Session, t["G"], sid["G"])
    assert (r["total_students"], r["my_rank"], r["my_score"]) == (1, 1, 3) and "subjects" not in r and "occurrence_id" not in r


def test_the_route_returns_the_occurrence_ranking_to_a_signed_in_participant(world):
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
    app.dependency_overrides[get_current_student] = lambda: sid["D"]
    try:
        with TestClient(app) as c:
            body = c.get(f"/ranking/{t['D']}").json()
            assert body["total_students"] == 4 and body["my_rank"] == 3 and [s["subject"] for s in body["subjects"]] == ["Biology", "Physics", "Chemistry"]
            app.dependency_overrides[get_current_student] = lambda: sid["F"]
            assert c.get(f"/ranking/{t['D']}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_an_evaluated_authorized_retake_replaces_its_original_in_the_rankings(world):
    """The original counts until the retake is evaluated; then only the retake counts (the original row is kept)."""
    Session, sid, t = world
    from sqlalchemy import text as sql
    with Session() as db:
        orig = db.execute(sql("select attempt_id from test_attempts where test_id = :t"), {"t": t["A"]}).scalar()
        before = ranking(Session, t["A"], sid["A"])
        assert (before["my_rank"], before["my_percentage"]) == (1, 100.0)
        retake = uuid.uuid4()
        db.execute(sql("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status, retake_of_attempt_id)
                          VALUES (:a, :t, :s, now(), now() + interval '1 hour', NULL, 'IN_PROGRESS', :o)"""), {"a": retake, "t": t["A"], "s": sid["A"], "o": orig})
        db.commit()
        assert ranking(Session, t["A"], sid["A"])["my_percentage"] == 100.0           # retake not evaluated yet: the original counts
        db.execute(sql("UPDATE test_attempts SET status = 'SUBMITTED', submitted_at = now() WHERE attempt_id = :a"), {"a": retake})
        db.execute(sql("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions, correct_answers,
                          incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, created_at)
                          VALUES (:r, :a, :t, :s, 4, 1, 1, 0, 3, 16, 4, 100, now())"""), {"r": uuid.uuid4(), "a": retake, "t": t["A"], "s": sid["A"]})
        db.commit()
    after = ranking(Session, t["A"], sid["A"])
    mine = [e for e in after["leaderboard"] if e["student_id"] == sid["A"]]
    assert len(mine) == 1 and (mine[0]["obtained_marks"], mine[0]["percentage"]) == (4, 25.0)    # one entry: the retake's
    assert after["total_students"] == 4
    phy = after["subjects"][1]
    assert [e["student_id"] for e in phy["leaderboard"]].count(sid["A"]) <= 1                    # never counted twice per subject
