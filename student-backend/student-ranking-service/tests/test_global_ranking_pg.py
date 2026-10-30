"""Global Test ranking (with paper choice: ONE test per occurrence, each attempt's paper snapshot), on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_global_ranking_pg.py

Overall: official obtained marks over each student's own delivered maximum, exact ratios, competition ranks (1, 1, 3). Per subject: only
the snapshot papers' questions, plus an attributed manual adjustment; a result with an unattributed adjustment ranks overall only."""
import json
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")


@pytest.fixture()
def world():
    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)
    run = uuid.uuid4().hex[:6].upper()
    P = {k: uuid.uuid4() for k in ("PA", "PB", "PC", "PD")}          # Physics A (2 q), Physics B (1 q), Chemistry C, Biology D
    layout = [("Physics", "PA"), ("Physics", "PA"), ("Physics", "PB"), ("Chemistry", "PC"), ("Biology", "PD")]
    sections = [{"position": 1, "subject": "Physics", "papers": [{"paper_id": str(P["PA"])}, {"paper_id": str(P["PB"])}]},
                {"position": 2, "subject": "Chemistry", "papers": [{"paper_id": str(P["PC"])}]},
                {"position": 3, "subject": "Biology", "papers": [{"paper_id": str(P["PD"])}]}]
    tid, occ, pr, br = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sid = {k: f"G{run}{k}" for k in "WXYZ"}
    q = {}
    with engine.begin() as c:
        for k, s in sid.items():
            c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                           "VALUES (:s, :f, 'Rank', :e, 'ACTIVE', now(), now())"), {"s": s, "f": f"Student{k}", "e": f"{s.lower()}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": pr, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": br, "p": pr})
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, global_occurrence_id, sections, created_at, updated_at) VALUES (:t, :p, :b, 'Global', 60, 5, 20,
                          now() - interval '3 hours', now() - interval '1 hour', 'ACTIVE', :o, CAST(:s AS jsonb), now(), now())"""),
                  {"t": tid, "p": pr, "b": br, "o": occ, "s": json.dumps(sections)})
        for order, (subject, k) in enumerate(layout, 1):
            q[order] = uuid.uuid4()
            c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, paper_id, question_text, question_type, option_a,
                              option_b, option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                              VALUES (:q, :t, :o, :s, :p, 'stem', 'MCQ', 'a', 'b', 'c', 'd', 'A', 4, 1, now(), now())"""),
                      {"q": q[order], "t": tid, "o": order, "s": subject, "p": P[k]})

    def sit(key, snap, answers, total, obtained, adjustments=None, manual=False):
        """An attempt with its snapshot and answers, and its stored official result (as the result service evaluated it)."""
        aid, rid = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as c:
            c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status)
                              VALUES (:a, :t, :s, now() - interval '2 hours', now() - interval '1 hour', now() - interval '1 hour', 'SUBMITTED')"""),
                      {"a": aid, "t": tid, "s": sid[key]})
            for subject, k in snap.items():
                c.execute(text("INSERT INTO attempt_selected_papers (attempt_id, subject, paper_id) VALUES (:a, :s, :p)"), {"a": aid, "s": subject, "p": P[k]})
            for order, ans in answers.items():
                c.execute(text("""INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, answered_at, updated_at, question_state)
                                  VALUES (:i, :a, :q, :x, now(), now(), 'ANSWERED')"""), {"i": uuid.uuid4(), "a": aid, "q": q[order], "x": ans})
            c.execute(text("""INSERT INTO test_results (result_id, attempt_id, test_id, student_id, total_questions, attempted_questions, correct_answers,
                              incorrect_answers, unanswered_questions, total_marks, obtained_marks, accuracy, subject_adjustments, created_at)
                              VALUES (:r, :a, :t, :s, 0, 0, 0, 0, 0, :m, :o, 0, CAST(:j AS jsonb), now())"""),
                      {"r": rid, "a": aid, "t": tid, "s": sid[key], "m": total, "o": obtained, "j": json.dumps(adjustments) if adjustments is not None else None})
            if manual:
                c.execute(text("""INSERT INTO result_history (history_id, result_id, test_id, student_id, kind, correction_ref, previous, new, reason, actor,
                                  created_at) VALUES (:h, :r, :t, :s, 'MARKS_ADJUSTMENT', :ref, '{}', '{}', 'manual review', 'SA001', now())"""),
                          {"h": uuid.uuid4(), "r": rid, "t": tid, "s": sid[key], "ref": uuid.uuid4().hex})

    sit("W", {"Physics": "PA", "Chemistry": "PC"}, {1: "A", 2: "A", 4: "A"}, 12, 12)                       # 12/12 = 100%
    sit("Z", {"Physics": "PB", "Chemistry": "PC"}, {3: "A", 4: "A"}, 8, 8)                                 # 8/8 = 100% (tie)
    sit("Y", {"Physics": "PA", "Chemistry": "PC", "Biology": "PD"}, {1: "A", 2: "A", 4: "A"}, 16, 14, manual=True)   # 12 + 2 unattributed
    sit("X", {"Physics": "PB", "Chemistry": "PC"}, {3: "A", 4: "B", 1: "A"}, 8, 4, {"Chemistry": 1}, manual=True)   # 3 + 1 attributed
    yield Session, sid, tid
    engine.dispose()


def test_one_overall_ranking_by_exact_ratio_with_shared_ranks(world):
    Session, sid, tid = world
    from app.services.ranking_service import get_ranking
    with Session() as db:
        r = get_ranking(db=db, test_id=tid, student_id=sid["X"])
    assert [(e["student_id"], e["rank"], e["obtained_marks"], e["max_marks"]) for e in r["leaderboard"]] == [
        (sid["W"], 1, 12, 12), (sid["Z"], 1, 8, 8), (sid["Y"], 3, 14, 16), (sid["X"], 4, 4, 8)]   # official marks, unattributed included
    assert r["my_rank"] == 4 and r["my_percentage"] == 50.0


def test_subject_rankings_use_the_snapshot_and_attribution_and_leave_out_unattributed(world):
    Session, sid, tid = world
    from app.services.ranking_service import get_ranking
    with Session() as db:
        subjects = {s["subject"]: s for s in get_ranking(db=db, test_id=tid, student_id=sid["X"])["subjects"]}
    table = {name: [(e["student_id"], e["obtained_marks"], e["max_marks"], e["rank"]) for e in s["leaderboard"]] for name, s in subjects.items()}
    # Physics: W sat paper A (2 questions, max 8), X and Z paper B (max 4); X's answer on paper A's question is not counted; Y left out
    assert table["Physics"] == [(sid["W"], 8, 8, 1), (sid["X"], 4, 4, 1), (sid["Z"], 4, 4, 1)]
    # Chemistry: X answered wrong (-1) but has an attributed +1 adjustment
    assert table["Chemistry"] == [(sid["W"], 4, 4, 1), (sid["Z"], 4, 4, 1), (sid["X"], 0, 4, 3)]
    assert table["Biology"] == []                                    # only Y sat it, and Y's adjustment is unattributed


def test_an_unattributed_adjustment_without_a_snapshot_also_leaves_subject_rankings(world):
    """A Global Test result made before paper choice (no snapshot) with an unattributed manual adjustment: overall only."""
    Session, sid, tid = world
    from sqlalchemy import text as sql
    from app.services.ranking_service import get_ranking
    with Session() as db:
        db.execute(sql("""DELETE FROM attempt_selected_papers WHERE attempt_id IN
                          (SELECT attempt_id FROM test_attempts WHERE test_id = :t AND student_id = :s)"""), {"t": tid, "s": sid["X"]})
        db.execute(sql("UPDATE test_results SET subject_adjustments = NULL WHERE test_id = :t AND student_id = :s"), {"t": tid, "s": sid["X"]})
        db.commit()
        r = get_ranking(db=db, test_id=tid, student_id=sid["X"])
    assert sid["X"] in [e["student_id"] for e in r["leaderboard"]]
    assert all(sid["X"] not in [e["student_id"] for e in s["leaderboard"]] for s in r["subjects"])
