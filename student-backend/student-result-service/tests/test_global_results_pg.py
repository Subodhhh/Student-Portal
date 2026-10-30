"""Global Test results (with paper choice: one test per occurrence, the attempt's paper snapshot), on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/stu_verify pytest tests/test_global_results_pg.py

Evaluation and every per-subject figure use only the attempt's snapshot papers; manual adjustments of any Global Test are attributed per subject
or kept unattributed, never guessed; a corrected paper re-scores only the attempts that sat it."""
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

KEY = {"X-Service-Key": "k-gt"}


@pytest.fixture()
def world(monkeypatch):
    from app.config import settings
    from app.database import get_db
    from app.main import app

    engine = create_engine(URL)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(settings, "institute_service_api_keys", "k-gt")
    run = uuid.uuid4().hex[:6].upper()
    P = {k: uuid.uuid4() for k in ("PA", "PB", "PC", "PD")}          # Physics A (2 q), Physics B (1 q), Chemistry C, Biology D
    papers = {"Physics": ["PA", "PB"], "Chemistry": ["PC"], "Biology": ["PD"]}
    sections = [{"position": i, "subject": s, "papers": [{"paper_id": str(P[k]), "label": k, "question_count": 2 if k == "PA" else 1,
                                                          "total_marks": 8 if k == "PA" else 4} for k in ks]}
                for i, (s, ks) in enumerate(papers.items(), start=1)]
    tid, pr, br = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    X, Y = f"V{run}X", f"V{run}Y"
    q = {}
    with engine.begin() as c:
        for s in (X, Y):
            c.execute(text("INSERT INTO students (student_id, first_name, last_name, email, status, created_at, updated_at) "
                           "VALUES (:s, :f, 'V2', :e, 'ACTIVE', now(), now())"), {"s": s, "f": s, "e": f"{s.lower()}@example.com"})
        c.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, 'P', :e, 'ACTIVE', now(), now())"),
                  {"p": pr, "e": f"p-{run}@example.com"})
        c.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'B', 'ACTIVE', now(), now())"),
                  {"b": br, "p": pr})
        c.execute(text("""INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, start_at, end_at,
                          status, global_occurrence_id, sections, created_at, updated_at) VALUES (:t, :p, :b, 'Global', 60, 5, 20,
                          now() - interval '3 hours', now() - interval '1 hour', 'ACTIVE', :o, CAST(:s AS jsonb), now(), now())"""),
                  {"t": tid, "p": pr, "b": br, "o": uuid.uuid4(), "s": json.dumps(sections)})
        for order, (subject, k) in enumerate([("Physics", "PA"), ("Physics", "PA"), ("Physics", "PB"), ("Chemistry", "PC"), ("Biology", "PD")], 1):
            q[order] = uuid.uuid4()
            c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, paper_id, question_text, question_type, option_a,
                              option_b, option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at)
                              VALUES (:q, :t, :o, :s, :p, 'stem', 'MCQ', 'a', 'b', 'c', 'd', 'A', 4, 1, now(), now())"""),
                      {"q": q[order], "t": tid, "o": order, "s": subject, "p": P[k]})
        for s, subject, status, msg in [(X, "Biology", "NOT_ENROLLED", "Not enrolled for Biology")]:
            c.execute(text("""INSERT INTO student_paper_eligibility (eligibility_id, test_id, student_id, subject, position, status, message, created_at)
                              VALUES (:i, :t, :s, :j, 3, :st, :m, now())"""), {"i": uuid.uuid4(), "t": tid, "s": s, "j": subject, "st": status, "m": msg})

    attempts = {}

    def sit(student, snap: dict[str, str], answers: dict[int, str]):
        aid = uuid.uuid4()
        with engine.begin() as c:
            c.execute(text("""INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status)
                              VALUES (:a, :t, :s, now() - interval '2 hours', now() - interval '1 hour', now() - interval '1 hour', 'SUBMITTED')"""),
                      {"a": aid, "t": tid, "s": student})
            for subject, k in snap.items():
                c.execute(text("INSERT INTO attempt_selected_papers (attempt_id, subject, paper_id) VALUES (:a, :s, :p)"), {"a": aid, "s": subject, "p": P[k]})
            for order, ans in answers.items():
                c.execute(text("""INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, answered_at, updated_at, question_state)
                                  VALUES (:i, :a, :q, :x, now(), now(), 'ANSWERED')"""), {"i": uuid.uuid4(), "a": aid, "q": q[order], "x": ans})
        attempts[student] = aid
        from app.services.result_service import evaluate_attempt
        with Session() as db:
            evaluate_attempt(db=db, attempt_id=aid, student_id=student)
        return aid

    # X sat Physics B + Chemistry C (+ a stray answer on paper A's question 1, which is not theirs); Y sat Physics A + C + Biology D
    sit(X, {"Physics": "PB", "Chemistry": "PC"}, {3: "A", 4: "B", 1: "A"})
    sit(Y, {"Physics": "PA", "Chemistry": "PC", "Biology": "PD"}, {1: "A", 2: "A", 4: "A"})
    client = TestClient(app)
    client.__enter__()
    yield {"engine": engine, "Session": Session, "tid": tid, "X": X, "Y": Y, "P": P, "q": q, "a": attempts, "c": client}
    client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    engine.dispose()


def result(w, student):
    with w["engine"].connect() as c:
        return c.execute(text("SELECT * FROM test_results WHERE attempt_id = :a"), {"a": w["a"][student]}).mappings().one()


def details(w, student):
    from app.services.result_service import get_result_details
    with w["Session"]() as db:
        return get_result_details(db=db, attempt_id=w["a"][student], student_id=student)


def adjust(w, student, obtained, subjects=None, ref=None):
    body = {"test_id": str(w["tid"]), "student_id": w[student], "obtained_marks": obtained, "reason": "manual review",
            "actor": "SA001", "correction_ref": ref or uuid.uuid4().hex}
    if subjects is not None:
        body["subjects"] = [{"subject": s, "obtained_marks": m} for s, m in subjects.items()]
    return w["c"].post("/internal/v1/results/adjust", json=body, headers=KEY)


def subject_rows(w, student):
    rows = w["c"].get(f"/internal/v1/tests/{w['tid']}/results/subjects", headers=KEY).json()
    return next(r for r in rows if r["student_id"] == w[student])


def test_evaluation_uses_only_the_snapshot_papers(world):
    w = world
    x, y = result(w, w["X"]), result(w, w["Y"])
    assert (x["total_questions"], x["total_marks"], x["obtained_marks"]) == (2, 8, 3)          # +4 (B) -1 (C); the stray answer ignored
    assert (y["total_questions"], y["total_marks"], y["obtained_marks"]) == (4, 16, 12)
    d = details(w, w["X"])
    assert [(s["subject"], s["status"], s.get("message")) for s in d["subjects"]] == [
        ("Physics", "PAPER", None), ("Chemistry", "PAPER", None), ("Biology", "NOT_ENROLLED", "Not enrolled for Biology")]
    assert [(s["subject"], s["total_marks"], s["obtained_marks"]) for s in d["subjects"]][:2] == [("Physics", 4, 4), ("Chemistry", 4, -1)]
    assert sorted(x["question_order"] for x in d["questions"]) == [3, 4] and d["unattributed_adjustment"] is None


def test_attributed_adjustment_keeps_subjects_and_overall_consistent(world):
    w = world
    r = adjust(w, "X", 6, {"Physics": 4, "Chemistry": 2})
    assert r.status_code == 200, r.text
    x = result(w, w["X"])
    assert x["obtained_marks"] == 6 and x["subject_adjustments"] == {"Chemistry": 3}
    d = details(w, w["X"])
    subj = {s["subject"]: (s["obtained_marks"], s["adjustment"]) for s in d["subjects"] if s["status"] == "PAPER"}
    assert subj == {"Physics": (4, 0), "Chemistry": (2, 3)} and sum(m for m, _ in subj.values()) == x["obtained_marks"]
    rows = subject_rows(w, "X")
    assert {s["subject"]: (s["obtained_marks"], s["adjustment"], s["paper_id"]) for s in rows["subjects"]} == {
        "Physics": (4, 0, str(w["P"]["PB"])), "Chemistry": (2, 3, str(w["P"]["PC"]))}
    assert rows["unattributed_adjustment"] is None
    with w["engine"].connect() as c:
        new = c.execute(text("SELECT new FROM result_history WHERE result_id = :r"), {"r": x["result_id"]}).scalar()
    assert new["subject_adjustments"] == {"Chemistry": 3} and new["obtained_marks"] == 6


def test_unattributed_adjustment_is_never_guessed_and_can_be_reattributed(world):
    w = world
    assert adjust(w, "Y", 14).status_code == 200                                               # several subjects, no attribution
    assert result(w, w["Y"])["subject_adjustments"] is None
    d = details(w, w["Y"])
    assert d["unattributed_adjustment"] == 2 and all(s.get("adjustment", 0) == 0 for s in d["subjects"])
    assert subject_rows(w, "Y")["unattributed_adjustment"] == 2
    r = adjust(w, "Y", 14, {"Physics": 8, "Chemistry": 4, "Biology": 2})                        # same total, now attributed
    assert r.status_code == 200, r.text
    assert result(w, w["Y"])["subject_adjustments"] == {"Biology": 2} and details(w, w["Y"])["unattributed_adjustment"] is None
    assert adjust(w, "Y", 14, {"Physics": 8, "Chemistry": 4, "Biology": 2}).status_code == 409  # nothing changes any more


def test_single_subject_attempt_is_attributed_without_a_choice(world):
    w = world
    eng = w["engine"]
    student = w["X"]
    with eng.begin() as c:     # make X's attempt a one-subject attempt (Physics B only) and re-score it
        c.execute(text("DELETE FROM attempt_selected_papers WHERE attempt_id = :a AND subject = 'Chemistry'"), {"a": w["a"][student]})
        c.execute(text("UPDATE test_results SET total_questions = 1, total_marks = 4, obtained_marks = 4 WHERE attempt_id = :a"), {"a": w["a"][student]})
    assert adjust(w, "X", 2).status_code == 200
    assert result(w, student)["subject_adjustments"] == {"Physics": -2}


@pytest.mark.parametrize("subjects, obtained", [
    ({"Physics": 4}, 4),                                    # a subject of the attempt missing
    ({"Physics": 4, "Chemistry": 2, "Biology": 0}, 6),      # a subject the attempt did not sit
    ({"Physics": 4, "Chemistry": 2}, 7),                    # does not add up
    ({"Physics": 5, "Chemistry": 1}, 6),                    # above the subject's maximum
])
def test_invalid_attributions_are_refused(world, subjects, obtained):
    w = world
    assert adjust(w, "X", obtained, subjects).status_code == 422
    assert result(w, w["X"])["obtained_marks"] == 3


def test_subject_marks_are_refused_for_a_test_that_is_not_a_global_test(world):
    w = world
    with w["engine"].begin() as c:
        c.execute(text("UPDATE tests SET sections = NULL, global_occurrence_id = NULL WHERE test_id = :t"), {"t": w["tid"]})
    assert adjust(w, "X", 6, {"Physics": 4, "Chemistry": 2}).status_code == 422
    assert adjust(w, "X", 6).status_code == 200 and result(w, w["X"])["subject_adjustments"] is None   # the existing behaviour


def test_a_corrected_paper_rescores_only_the_attempts_that_sat_it(world):
    w = world
    with w["engine"].begin() as c:
        c.execute(text("UPDATE questions SET correct_answer = 'B' WHERE question_id = :q"), {"q": w["q"][1]})   # paper A's question 1
    body = {"reason": "key fix", "actor": "SA001", "correction_ref": uuid.uuid4().hex, "paper_ids": [str(w["P"]["PA"])]}
    out = w["c"].post(f"/internal/v1/tests/{w['tid']}/reevaluate", json=body, headers=KEY).json()
    assert out["changed"] == [w["Y"]] and out["not_affected"] == 1
    assert result(w, w["Y"])["obtained_marks"] == 7 and result(w, w["X"])["obtained_marks"] == 3     # Y: 12 - 4 - 1
    with w["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM result_history WHERE result_id = :r"), {"r": result(w, w["X"])["result_id"]}).scalar() == 0


def test_a_global_test_without_paper_choice_is_attributed_and_rescored_too(world):
    """A Global Test made for one combination of papers (no snapshot; every question of the test was delivered)."""
    w = world
    with w["engine"].begin() as c:     # X's attempt as before paper choice: no snapshot, an old-style section list
        c.execute(text("DELETE FROM attempt_selected_papers WHERE attempt_id = :a"), {"a": w["a"][w["X"]]})
        c.execute(text("UPDATE test_results SET total_questions = 5, total_marks = 20, obtained_marks = 7 WHERE attempt_id = :a"),
                  {"a": w["a"][w["X"]]})                                    # as evaluated on every question of the test
        c.execute(text("""UPDATE tests SET sections = CAST(:s AS jsonb) WHERE test_id = :t"""), {"t": w["tid"], "s": json.dumps([
            {"position": 1, "subject": "Physics", "status": "PAPER", "message": None, "question_count": 3},
            {"position": 2, "subject": "Chemistry", "status": "PAPER", "message": None, "question_count": 1},
            {"position": 3, "subject": "Biology", "status": "PAPER", "message": None, "question_count": 1}])})
    # X answered 1 (A, right), 3 (A, right), 4 (B, wrong) of all five questions: Physics 8, Chemistry -1, Biology 0
    assert adjust(w, "X", 9, {"Physics": 8, "Chemistry": 1, "Biology": 0}).status_code == 200
    assert result(w, w["X"])["subject_adjustments"] == {"Chemistry": 2}
    with w["engine"].begin() as c:
        c.execute(text("UPDATE questions SET correct_answer = 'B' WHERE question_id = :q"), {"q": w["q"][4]})   # Chemistry's key
    body = {"reason": "key fix", "actor": "SA001", "correction_ref": uuid.uuid4().hex, "paper_ids": [str(w["P"]["PC"])]}
    out = w["c"].post(f"/internal/v1/tests/{w['tid']}/reevaluate", json=body, headers=KEY).json()
    assert out["changed"] == [w["Y"]] and out["skipped_manual"] == [w["X"]] and out["not_affected"] == 0   # X counted as a taker
