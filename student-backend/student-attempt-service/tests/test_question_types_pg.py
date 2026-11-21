"""MCQ, MSQ and NAT answers in the attempt service, on a DISPOSABLE PostgreSQL only (skipped otherwise):

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5439/stu_qt_verify pytest tests/test_question_types_pg.py

Every saved answer is checked against its question's type with the shared rulebook (decision D12) and stored canonical; anything
invalid is refused with 422 and never stored; marking for review keeps the answer; the workspace gives MSQ options / NAT format but
never a key. The shared golden vectors run against this service's copy of the rulebook too."""
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app import question_types as qt
from tests.test_sections_pg import env, pytestmark  # noqa: F401  (the same disposable-PostgreSQL fixture)

GOLDEN = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "question-types" / "golden.json").read_text())
MSQ_CONTENT = {"options": [{"id": x, "text": x.lower(), "image": None} for x in "ABCDE"]}
NAT_CONTENT = {"format": "DECIMAL", "decimals": 2, "unit": "m"}


def test_golden_vectors_against_this_copy():
    for case in GOLDEN["answers"]:
        if case.get("error"):
            with pytest.raises(qt.QuestionTypeError):
                qt.normalise_answer(case["type"], case["raw"], content=case.get("content"))
        else:
            assert qt.normalise_answer(case["type"], case["raw"], content=case.get("content")) == case["expect"]
    for case in GOLDEN["scores"]:
        assert qt.score(case["type"], case["key"], case["answer"], Decimal(case["marks"]), Decimal(case["negative"])) == (
            case["status"], Decimal(case["marks_obtained"]))


def typed(engine, tid, order, qtype, content, key):
    q = uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("""INSERT INTO questions (question_id, test_id, question_order, subject, question_text, question_type, content, answer_key,
                          scheme, correct_answer, marks, negative_marks, created_at, updated_at) VALUES (:q, :t, :o, 'Physics', 'typed', :ty,
                          CAST(:c AS jsonb), CAST(:k AS jsonb), :s, '', 4, 0, now(), now())"""),
                  {"q": q, "t": tid, "o": order, "ty": qtype, "c": json.dumps(content), "k": json.dumps(key), "s": qt.SCHEMES[qtype]})
    return q


def test_answers_are_validated_canonicalised_and_never_stored_when_invalid(env):
    clients, engine, make_test, _ = env
    tid, (mcq,) = make_test({"Physics": 1})
    msq = typed(engine, tid, 2, "MCQ_MULTI", MSQ_CONTENT, {"correct": ["A", "C"]})
    nat = typed(engine, tid, 3, "NUMERICAL", NAT_CONTENT, {"value": "9.8", "tolerance": {"kind": "EXACT"}})
    c = clients[0]
    attempt = c.post("/attempts/start", json={"test_id": str(tid)}).json()["attempt_id"]

    def save(q, answer):
        return c.post(f"/attempts/{attempt}/answers", json={"question_id": str(q), "selected_answer": answer})

    def stored(q):
        with engine.connect() as conn:
            return conn.execute(text("SELECT selected_answer FROM attempt_answers WHERE attempt_id = :a AND question_id = :q"),
                                {"a": attempt, "q": q}).scalar()

    for q, bad in ((mcq, "Z"), (mcq, "AB"), (msq, "AF"), (msq, "AA"), (nat, "1e3"), (nat, "NaN"), (nat, "1,000"), (nat, "9.8m"),
                   (nat, "x" * 70)):
        r = save(q, bad)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_ANSWER", (bad, r.text)
        assert stored(q) is None                                               # nothing invalid is ever stored
    assert save(mcq, "b").status_code == 200 and stored(mcq) == "B"
    assert save(msq, "c, a").status_code == 200 and stored(msq) == "AC"
    assert save(nat, "9.80").status_code == 200 and stored(nat) == "9.8"
    assert save(nat, "-0.50").status_code == 200 and stored(nat) == "-0.5"
    assert save(msq, "Z").status_code == 422 and stored(msq) == "AC"             # a refused change keeps the previous answer
    assert c.post(f"/attempts/{attempt}/questions/{msq}/mark-review").status_code == 200 and stored(msq) == "AC"   # review keeps it
    ws = c.get(f"/attempts/{attempt}/workspace")
    body = ws.text
    for secret in ("answer_key", "correct_answer", "scheme", "ALL_OR_NOTHING", '"correct"'):
        assert secret not in body, secret
    qs = {q["question_id"]: q for q in ws.json()["questions"]}
    assert qs[str(msq)]["content"] == MSQ_CONTENT and qs[str(nat)]["content"] == NAT_CONTENT and qs[str(msq)]["selected_answer"] == "AC"
