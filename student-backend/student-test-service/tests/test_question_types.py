"""MCQ, MSQ and NAT in the Student test service (docs/institute-portal/QUESTION_TYPES_ARCHITECTURE.md): the shared rulebook copy
against the golden vectors, typed questions published (payload v2) and stored, a student gets their content but NEVER their key,
option pictures are authorised, and answer-key corrections are typed.
Run with dummy env and an in-memory DB:  DATABASE_URL=sqlite:// pytest tests/test_question_types.py"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import question_types as qt
from app.auth import get_current_student
from app.main import app
from app.models import question as question_models
from tests.test_internal_exams import H, env, payload  # noqa: F401  (shared fixtures)

GOLDEN = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "question-types" / "golden.json").read_text())
PNG = b"\x89PNG\r\n\x1a\n" + b"option picture"
SHA = hashlib.sha256(PNG).hexdigest()


@pytest.mark.parametrize("case", GOLDEN["answers"])
def test_golden_answers(case):
    if case.get("error"):
        with pytest.raises(qt.QuestionTypeError):
            qt.normalise_answer(case["type"], case["raw"], content=case.get("content"))
    else:
        assert qt.normalise_answer(case["type"], case["raw"], content=case.get("content")) == case["expect"]


@pytest.mark.parametrize("case", GOLDEN["scores"])
def test_golden_scores(case):
    assert qt.score(case["type"], case["key"], case["answer"], Decimal(case["marks"]), Decimal(case["negative"])) == (
        case["status"], Decimal(case["marks_obtained"]))


def msq(order=1, **over):
    q = {"order": order, "subject": "Physics", "text": "Which are vectors?", "type": "MCQ_MULTI",
         "content": {"options": [{"id": "A", "text": "Velocity"}, {"id": "B", "text": "Speed"}, {"id": "C", "text": "Force"},
                                 {"id": "D", "text": "", "image": SHA}, {"id": "E", "text": "Mass"}]},
         "key": {"correct": ["C", "A"]}, "scheme": "ALL_OR_NOTHING", "marks": 4, "negative_marks": 0}
    q.update(over)
    return q


def nat(order=2, **over):
    q = {"order": order, "subject": "Physics", "text": "g in m/s^2?", "type": "NUMERICAL",
         "content": {"format": "DECIMAL", "decimals": 2, "unit": "m/s^2"},
         "key": {"value": "9.80", "tolerance": {"kind": "ABS", "amount": "0.05"}}, "scheme": "NUMERIC_TOLERANCE",
         "marks": "4", "negative_marks": "1"}
    q.update(over)
    return q


def typed_exam(**over):
    body = payload(**over)
    body["questions"] = [body["questions"][0], {**msq(2)}, {**nat(3)}]
    return body


def test_typed_questions_are_stored_in_canonical_form_and_mcq_rows_are_unchanged(env):
    client, Session = env
    assert client.put(f"/internal/v1/question-images/{SHA}", content=PNG, headers={**H, "Content-Type": "image/png"}).status_code == 201
    body = typed_exam()
    r = client.post("/internal/v1/exams", json=body, headers=H)
    assert r.status_code == 201, r.text
    with Session() as db:
        rows = {q.question_order: q for q in db.query(question_models.Question)}
        assert (rows[1].question_type, rows[1].correct_answer, rows[1].content, rows[1].answer_key) == ("MCQ", "A", None, None)
        assert rows[2].question_type == "MCQ_MULTI" and rows[2].answer_key == {"correct": ["A", "C"]} and rows[2].correct_answer == ""
        assert rows[2].option_a is None and rows[2].scheme == "ALL_OR_NOTHING"
        assert rows[3].answer_key == {"value": "9.8", "tolerance": {"kind": "ABS", "amount": "0.05"}}
        pics = db.query(question_models.QuestionPicture).all()
        assert [(p.role, p.checksum) for p in pics] == [("option_d", SHA)]


@pytest.mark.parametrize("change,detail", [
    ({"key": {"correct": ["Z"]}}, "Correct option Z"),
    ({"scheme": "PARTIAL"}, "scored ALL_OR_NOTHING"),
    ({"negative_marks": 1}, "never lose marks"),
    ({"options": {"A": "a"}}, "no options or correct letter"),
    ({"correct": "A"}, "no options or correct letter"),
    ({"content": {"options": [{"id": "A", "text": "x"}]}}, "2 to 8 options"),
])
def test_invalid_msq_payloads_are_refused_and_nothing_is_stored(env, change, detail):
    client, Session = env
    body = payload()
    body["questions"] = [msq(1, **change)]
    r = client.post("/internal/v1/exams", json=body, headers=H)
    assert r.status_code == 422 and detail in r.text
    with Session() as db:
        assert db.query(question_models.Question).count() == 0


def test_invalid_nat_payloads_are_refused(env):
    client, _ = env
    for change in ({"key": {"value": "1e3"}}, {"key": {"value": "1.234"}}, {"content": {"format": "INTEGER"}, "key": {"value": "2.5"}},
                   {"key": {"value": "5", "tolerance": {"kind": "RANGE", "min": "6", "max": "7"}}}):
        body = payload()
        body["questions"] = [nat(1, **change)]
        assert client.post("/internal/v1/exams", json=body, headers=H).status_code == 422, change


def test_a_student_gets_the_content_but_never_the_key(env, monkeypatch):
    from app.routes import test as test_routes
    from app.schemas.test import TestStartResponse
    from app.services import test_service
    from tests.test_global_exams import _row
    client, Session = env
    client.put(f"/internal/v1/question-images/{SHA}", content=PNG, headers={**H, "Content-Type": "image/png"})
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    body = typed_exam(start_at=start.isoformat(), end_at=(start + timedelta(hours=6)).isoformat(), student_ids=["S1"])
    assert client.post("/internal/v1/exams", json=body, headers=H).status_code == 201
    tid = uuid.UUID(body["exam_version_id"])
    monkeypatch.setattr(test_service, "get_test_by_id", lambda **kw: {**_row(tid, None), "global_occurrence_id": None, "sections": None})
    with Session() as db:
        served = test_service.get_test_for_start(db, tid, "S1")
    text = TestStartResponse.model_validate(served).model_dump_json()       # exactly what the API returns (its response model)
    for secret in ("answer_key", "correct_answer", "\"correct\"", "scheme", "9.8", "tolerance", "ALL_OR_NOTHING"):
        assert secret not in text, secret                                     # no key of any type reaches the browser
    questions = {q["question_order"]: q for q in json.loads(text)["questions"]}
    assert questions[2]["question_type"] == "MCQ_MULTI" and len(questions[2]["content"]["options"]) == 5
    assert questions[3]["content"] == {"format": "DECIMAL", "decimals": 2, "unit": "m/s^2"}
    assert questions[1]["content"] is None and questions[1]["option_a"] == "a"
    monkeypatch.setattr(test_routes, "get_test_for_student", lambda db, test_id, student_id: True)
    app.dependency_overrides[get_current_student] = lambda: "S1"
    assert client.get(f"/tests/{tid}/images/{SHA}").status_code == 200      # an MSQ option picture is authorised
    app.dependency_overrides.pop(get_current_student, None)


def test_answer_key_corrections_are_typed(env):
    client, Session = env
    client.put(f"/internal/v1/question-images/{SHA}", content=PNG, headers={**H, "Content-Type": "image/png"})
    body = typed_exam()
    client.post("/internal/v1/exams", json=body, headers=H)
    url = f"/internal/v1/exams/{body['exam_version_id']}/answer-key"
    meta = {"reason": "key was wrong", "actor": "STF000001", "correction_ref": str(uuid.uuid4())}
    assert client.post(url, json={"changes": [{"question_order": 2, "correct": "A"}], **meta}, headers=H).status_code == 422
    assert client.post(url, json={"changes": [{"question_order": 1, "key": {"correct": ["A"]}}], **meta}, headers=H).status_code == 422
    assert client.post(url, json={"changes": [{"question_order": 2, "key": {"correct": ["Q"]}}], **meta}, headers=H).status_code == 422
    r = client.post(url, json={"changes": [{"question_order": 2, "key": {"correct": ["A"]}},
                                           {"question_order": 3, "key": {"value": "9.81", "tolerance": {"kind": "EXACT"}}},
                                           {"question_order": 1, "correct": "B"}], **meta}, headers=H)
    assert r.status_code == 200, r.text
    with Session() as db:
        rows = {q.question_order: q for q in db.query(question_models.Question)}
        assert rows[2].answer_key == {"correct": ["A"]} and rows[3].answer_key["value"] == "9.81" and rows[1].correct_answer == "B"


def test_the_institute_delivery_contract_is_accepted_and_scored_as_the_institute_expects(env):
    """T6: contracts/question-types/delivery_v2.json is exactly what the Institute exam service sends (pinned by its own test); this
    service stores it, serves it without keys, and the shared rulebook scores its MSQ / NAT keys as the result service will."""
    client, Session = env
    delivered = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "question-types" / "delivery_v2.json").read_text())
    body = payload()
    body["questions"] = delivered
    body["total_questions"] = len(delivered)
    body["total_marks"] = sum(int(q["marks"]) for q in delivered)
    r = client.post("/internal/v1/exams", json=body, headers=H)
    assert r.status_code == 201, r.text
    with Session() as db:
        rows = {q.question_order: q for q in db.query(question_models.Question)}
        assert [rows[n].question_type for n in (1, 2, 3)] == ["MCQ", "MCQ_MULTI", "NUMERICAL"]
        msq_key, nat_key = rows[2].answer_key, rows[3].answer_key
        assert (rows[2].negative_marks, rows[3].negative_marks) == (0, 1)
    # the rulebook (the result service's scorer) on the delivered keys
    assert qt.score("MCQ_MULTI", msq_key, qt.normalise_answer("MCQ_MULTI", "C,A"), Decimal(4), Decimal(0)) == ("CORRECT", Decimal(4))
    assert qt.score("MCQ_MULTI", msq_key, "A", Decimal(4), Decimal(0)) == ("INCORRECT", Decimal(0))          # never negative
    assert qt.score("NUMERICAL", nat_key, qt.normalise_answer("NUMERICAL", "9.85", content=delivered[2]["content"]),
                    Decimal(4), Decimal(1)) == ("CORRECT", Decimal(4))                                         # inclusive tolerance
    assert qt.score("NUMERICAL", nat_key, "9.86", Decimal(4), Decimal(1)) == ("INCORRECT", Decimal(-1))
