"""MCQ, MSQ and NAT evaluation in the result service, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5439/stu_qt_verify pytest tests/test_question_types_pg.py

One attempt answering every type is evaluated by the production `evaluate_attempt`: totals, the stored per-question marks (D9) and the
question review all come from the ONE scorer; MSQ is all-or-nothing and never negative; NAT uses the key's tolerance; re-evaluation
after a typed key correction equals a fresh evaluation and rewrites the per-question rows."""
import json
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app import question_types as qt
from app.models.test_result import TestResult
from app.models.test_result_question import TestResultQuestion
from app.services import reevaluation
from app.services.result_service import evaluate_attempt, get_result_details
from tests.test_corrections_pg import URL, db, pytestmark  # noqa: F401  (the same disposable-PostgreSQL fixture)

GOLDEN = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "question-types" / "golden.json").read_text())
MSQ = {"options": [{"id": x, "text": x.lower(), "image": None} for x in "ABCD"]}
NAT = {"format": "DECIMAL", "decimals": 2, "unit": "m/s^2"}
# (type, content, key, correct letter, marks, negative)
QUESTIONS = [("MCQ", None, None, "B", 4, 1),
             ("MCQ_MULTI", MSQ, {"correct": ["A", "C"]}, "", 4, 0),
             ("MCQ_MULTI", MSQ, {"correct": ["B"]}, "", 4, 0),
             ("NUMERICAL", NAT, {"value": "9.8", "tolerance": {"kind": "ABS", "amount": "0.05"}}, "", 4, 1),
             ("NUMERICAL", NAT, {"value": "3", "tolerance": {"kind": "EXACT"}}, "", 4, 1),
             ("NUMERICAL", NAT, {"value": "1", "tolerance": {"kind": "EXACT"}}, "", 4, 1)]
ANSWERS = ["B", "AC", "AB", "9.84", "4", None]          # right, right, wrong (0), within tolerance, wrong (-1), unanswered


def test_golden_vectors_against_this_copy():
    for case in GOLDEN["scores"]:
        assert qt.score(case["type"], case["key"], case["answer"], Decimal(case["marks"]), Decimal(case["negative"])) == (
            case["status"], Decimal(case["marks_obtained"]))


def seed(db):
    provider, branch, test = (uuid.uuid4() for _ in range(3))
    db.execute(text("INSERT INTO providers (provider_id, name, email, status, created_at, updated_at) VALUES (:p, :n, :e, 'ACTIVE', now(), now())"),
               {"p": provider, "n": f"types {provider}", "e": f"{provider}@example.test"})
    db.execute(text("INSERT INTO branches (branch_id, provider_id, name, status, created_at, updated_at) VALUES (:b, :p, 'b', 'ACTIVE', now(), now())"),
               {"b": branch, "p": provider})
    db.execute(text("INSERT INTO tests (test_id, provider_id, branch_id, name, duration_minutes, total_questions, total_marks, status, created_at, "
                    "updated_at, start_at, end_at) VALUES (:t, :p, :b, 'types', 60, 6, 24, 'ACTIVE', now(), now(), now() - interval '3 hours', "
                    "now() - interval '1 hour')"), {"t": test, "p": provider, "b": branch})
    qids = []
    for order, (qtype, content, key, letter, marks, negative) in enumerate(QUESTIONS, start=1):
        qid = uuid.uuid4()
        qids.append(qid)
        db.execute(text("INSERT INTO questions (question_id, test_id, question_order, question_text, question_type, content, answer_key, scheme, "
                        "option_a, option_b, option_c, option_d, correct_answer, marks, negative_marks, created_at, updated_at, subject) VALUES "
                        "(:q, :t, :o, 'q', :ty, CAST(:c AS jsonb), CAST(:k AS jsonb), :s, :oa, :oa, :oa, :oa, :l, :m, :n, now(), now(), 'Physics')"),
                   {"q": qid, "t": test, "o": order, "ty": qtype, "c": json.dumps(content) if content else None,
                    "k": json.dumps(key) if key else None, "s": qt.SCHEMES.get(qtype), "oa": "x" if qtype == "MCQ" else None,
                    "l": letter, "m": marks, "n": negative})
    attempt, student = uuid.uuid4(), f"STQ{uuid.uuid4().hex[:6].upper()}"
    db.execute(text("INSERT INTO test_attempts (attempt_id, test_id, student_id, started_at, expires_at, submitted_at, status) "
                    "VALUES (:a, :t, :s, now(), now() + interval '1 hour', now(), 'SUBMITTED')"), {"a": attempt, "t": test, "s": student})
    for qid, answer in zip(qids, ANSWERS):
        if answer is not None:
            db.execute(text("INSERT INTO attempt_answers (answer_id, attempt_id, question_id, selected_answer, answered_at, updated_at, question_state) "
                            "VALUES (:i, :a, :q, :c, now(), now(), 'ANSWERED')"), {"i": uuid.uuid4(), "a": attempt, "q": qid, "c": answer})
    db.commit()
    return test, attempt, student, qids


def test_every_type_is_scored_by_the_one_scorer_and_stored_per_question(db):
    test, attempt, student, qids = seed(db)
    result = evaluate_attempt(db, attempt, student)
    # +4 (MCQ) +4 (MSQ exact) +0 (MSQ wrong: never negative) +4 (NAT within ±0.05) -1 (NAT wrong) +0 (unanswered) = 11
    assert (result.obtained_marks, result.correct_answers, result.incorrect_answers, result.unanswered_questions) == (Decimal(11), 3, 2, 1)
    rows = {r.question_order: r for r in db.query(TestResultQuestion).filter(TestResultQuestion.result_id == result.result_id)}
    assert [(rows[o].status, rows[o].marks_obtained) for o in range(1, 7)] == [
        ("CORRECT", 4), ("CORRECT", 4), ("INCORRECT", 0), ("CORRECT", 4), ("INCORRECT", -1), ("UNANSWERED", 0)]
    assert rows[2].question_type == "MCQ_MULTI" and rows[2].selected_answer == "AC" and rows[1].question_type == "MCQ_SINGLE"
    details = get_result_details(db, attempt, student)
    review = {q["question_order"]: q for q in details["questions"]}
    assert sum((q["marks_obtained"] for q in review.values()), Decimal(0)) == result.obtained_marks      # review = stored result
    assert review[2]["question_type"] == "MCQ_MULTI" and review[2]["correct_answer"] == "A, C" and review[2]["answer_key"] == {"correct": ["A", "C"]}
    assert review[4]["correct_answer"] == "9.8 ± 0.05" and review[4]["content"] == NAT
    assert review[1]["correct_answer"] == "B" and review[1]["answer_key"] is None


def test_a_typed_key_correction_re_evaluates_like_a_fresh_evaluation(db):
    test, attempt, student, qids = seed(db)
    first = evaluate_attempt(db, attempt, student)
    db.execute(text("UPDATE questions SET answer_key = CAST(:k AS jsonb) WHERE question_id = :q"),
               {"k": json.dumps({"correct": ["A", "B"]}), "q": qids[2]})            # the second MSQ: AB was in fact right
    db.commit()
    ref = f"types-{uuid.uuid4()}"
    out = reevaluation.reevaluate_test(db, test, correction_ref=ref, reason="MSQ key corrected", actor="STF000001")
    assert out["changed"] == [student]
    after = db.get(TestResult, first.result_id)
    db.refresh(after)
    assert after.obtained_marks == Decimal(15) and after.correct_answers == 4
    rows = {r.question_order: r for r in db.query(TestResultQuestion).filter(TestResultQuestion.result_id == first.result_id)}
    assert (rows[3].status, rows[3].marks_obtained) == ("CORRECT", 4) and len(rows) == 6        # per-question rows rewritten
