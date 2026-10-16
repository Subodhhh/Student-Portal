"""Real-SQL parity check for re-evaluation, on a DISPOSABLE PostgreSQL only (skipped otherwise).

    PGURL=postgresql+psycopg://postgres@127.0.0.1:5439/inst_verify scripts/verify-institute-on-postgres.sh   # runs this too

Two identical tests are scored differently: test A is evaluated with the old answer key by the production `evaluate_attempt`,
the key is then corrected and `reevaluate_test` runs; test B is evaluated by `evaluate_attempt` with the corrected key from the
start. Both must end with identical results for every student, which proves the re-evaluation uses the same SQL and rules.
(result_history is append-only by design, so this test leaves its uniquely-identified rows in the disposable database.)"""
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.result_history import ResultHistory
from app.models import test_result as result_model
from app.services import reevaluation
from app.services.result_service import evaluate_attempt

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not (URL.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", URL)),
    reason="needs a disposable local PostgreSQL in TEST_DATABASE_URL")

OLD_KEY = ["A", "B", "C", "D"]
NEW_KEY = ["A", "D", "C", "B"]                       # Q2 and Q4 corrected
ANSWERS = {                                            # selected answers per question order (None = unanswered)
    "STU900001": ["A", "B", "C", "D"],
    "STU900002": [" a ", "d", None, "b"],              # padding/case are ignored; one unanswered
    "STU900003": [None, None, None, None],
    "STU900004": ["B", "A", "D", "C"],                 # all wrong
}


@pytest.fixture()
def db():
    engine = create_engine(URL)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def seed(db, key: list[str]) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    provider, branch, test = (uuid.uuid4() for _ in range(3))
    db.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, :n, :e, 'ACTIVE', now(), now())"),
               {"p": provider, "n": f"parity {provider}", "e": f"{provider}@example.test"})
    db.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'b', 'ACTIVE', now(), now())"),
               {"b": branch, "p": provider})
    db.execute(text("INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, status, created_at, updated_at) "
                    "VALUES (:t, :p, :b, 'parity', 60, 4, 16, 'PUBLISHED', now(), now())"), {"t": test, "p": provider, "b": branch})
    qids = []
    for order, right in enumerate(key, start=1):
        qid = uuid.uuid4()
        qids.append(qid)
        db.execute(text("INSERT INTO questions (question_id, test_id, question_order, question_text, question_type, option_a, option_b, option_c, option_d, "
                        "correct_answer, marks, negative_marks, created_at, updated_at, subject) VALUES (:q, :t, :o, 'q', 'MCQ', 'a', 'b', 'c', 'd', :c, 4, 1, now(), now(), 'S')"),
                   {"q": qid, "t": test, "o": order, "c": right})
    attempts = {}
    for student, selected in ANSWERS.items():
        attempt = uuid.uuid4()
        attempts[student] = attempt
        db.execute(text("INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status) "
                        "VALUES (:a, :t, :s, now(), now() + interval '1 hour', now(), 'SUBMITTED')"), {"a": attempt, "t": test, "s": student})
        for qid, choice in zip(qids, selected):
            if choice is not None:
                db.execute(text("INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, answered_at, updated_at, question_state) "
                                "VALUES (:i, :a, :q, :c, now(), now(), 'ANSWERED')"), {"i": uuid.uuid4(), "a": attempt, "q": qid, "c": choice})
    db.commit()
    return test, attempts


def results(db, test_id) -> dict:
    rows = db.query(result_model.TestResult).filter(result_model.TestResult.test_id == test_id).all()
    return {r.student_id: reevaluation.snapshot(r) for r in rows}


def test_reevaluation_equals_a_fresh_evaluation_with_the_corrected_key(db):
    test_a, attempts_a = seed(db, OLD_KEY)
    for student, attempt in attempts_a.items():
        evaluate_attempt(db, attempt, student)
    before = results(db, test_a)

    db.execute(text("UPDATE questions SET correct_answer = :c WHERE test_id = :t AND question_order = :o"), {"c": "D", "t": test_a, "o": 2})
    db.execute(text("UPDATE questions SET correct_answer = :c WHERE test_id = :t AND question_order = :o"), {"c": "B", "t": test_a, "o": 4})
    db.commit()
    ref = f"parity-{uuid.uuid4()}"
    out = reevaluation.reevaluate_test(db, test_a, correction_ref=ref, reason="answer key corrected", actor="STF000001")

    test_b, attempts_b = seed(db, NEW_KEY)
    for student, attempt in attempts_b.items():
        evaluate_attempt(db, attempt, student)

    after, fresh = results(db, test_a), results(db, test_b)
    assert after == fresh                                                       # identical to a first-time evaluation
    assert before != after and sorted(out["changed"]) == ["STU900001", "STU900002"]   # 003 answered nothing, 004 was wrong either way
    assert out["unchanged"] == 2 and out["skipped_manual"] == [] and out["no_questions"] == 0

    history = db.query(ResultHistory).filter(ResultHistory.test_id == test_a).all()
    assert len(history) == 2 and all(h.kind == "REEVALUATION" and h.correction_ref == ref for h in history)
    by_student = {h.student_id: h for h in history}
    assert by_student["STU900001"].previous == before["STU900001"] and by_student["STU900001"].new == after["STU900001"]

    again = reevaluation.reevaluate_test(db, test_a, correction_ref=ref, reason="answer key corrected", actor="STF000001")
    assert again["changed"] == [] and again["already_applied"] == 2             # replay changes nothing
    assert results(db, test_a) == after


def test_corrected_marks_are_what_students_and_the_ranking_service_read(db):
    """The EXISTING student-facing query and the ranking service's own SQL read test_results live, so corrections are visible at once."""
    import importlib.util
    from pathlib import Path

    from app.repositories.result_repository import get_student_results
    spec = importlib.util.spec_from_file_location(
        "ranking_repository", Path(__file__).resolve().parents[2] / "student-ranking-service" / "app" / "repositories" / "ranking_repository.py")
    ranking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ranking)

    test_id, attempts = seed(db, OLD_KEY)
    for student, attempt in attempts.items():
        evaluate_attempt(db, attempt, student)
    top_before = [r["student_id"] for r in ranking.get_test_rankings(db, test_id)][0]
    assert top_before == "STU900001"                                           # all four correct

    row = db.query(result_model.TestResult).filter_by(test_id=test_id, student_id="STU900001").one()
    reevaluation.apply(db, row, {"obtained_marks": -9}, kind="MARKS_ADJUSTMENT", correction_ref=f"vis-{uuid.uuid4()}", reason="manual review", actor="STF000001")
    db.commit()

    mine = [r for r in get_student_results(db, "STU900001") if r["test_id"] == test_id]
    assert len(mine) == 1 and mine[0]["obtained_marks"] == -9                  # the student's own result view
    ranked = [(r["student_id"], r["obtained_marks"]) for r in ranking.get_test_rankings(db, test_id)]
    assert ranked[-1] == ("STU900001", -9) and ranked[0][0] != "STU900001"     # the ranking moved
