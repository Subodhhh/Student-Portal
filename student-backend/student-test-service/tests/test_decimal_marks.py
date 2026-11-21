"""Decimal marks: delivery accepts 4, 0.25 and "0.25", rejects bad marks; whole marks serialize as before.

Run with dummy env and an in-memory DB (never the configured one):
   DATABASE_URL=sqlite:// pytest tests/test_decimal_marks.py"""
import uuid
import warnings
from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.marks import Marks
from app.models import question as question_models
from app.models import test as test_models
from tests.test_internal_exams import H, env, payload  # noqa: F401  (env is a pytest fixture)

warnings.filterwarnings("ignore", message="Dialect sqlite.*Decimal")


class Box(BaseModel):
    m: Marks


@pytest.mark.parametrize("raw, expected", [(4, "4"), (0.25, "0.25"), ("0.25", "0.25"), ("4.50", "4.5"), (0, "0"), ("1.25", "1.25")])
def test_accepts_integer_number_and_string(raw, expected):
    assert Box(m=raw).m == Decimal(expected)


@pytest.mark.parametrize("raw", ["0.255", 0.255, "NaN", "Infinity", "-Infinity", True, False, None, "abc", "", [1], 1e400])
def test_rejects_bad_marks(raw):
    with pytest.raises(ValueError):
        Box(m=raw)


def test_serializes_whole_as_integer_and_fraction_as_number():
    assert Box(m=Decimal("4.00")).model_dump_json() == '{"m":4}'
    assert Box(m=Decimal("0.25")).model_dump_json() == '{"m":0.25}'
    assert Box(m=Decimal("-1.50")).model_dump_json() == '{"m":-1.5}'


def questions(*marks):
    return [{"order": i + 1, "subject": "Physics", "text": f"Q{i}", "options": {"A": "a", "B": "b", "C": "c", "D": "d"},
             "correct": "A", "marks": m, "negative_marks": n} for i, (m, n) in enumerate(marks)]


def test_delivery_stores_exact_decimals_and_exact_total(env):
    c, Session = env
    body = payload(questions=questions((1.25, "0.25"), ("1.25", 0.5), (4, 1)))
    assert c.post("/internal/v1/exams", json=body, headers=H).status_code == 201
    with Session() as db:
        test = db.get(test_models.Test, uuid.UUID(body["exam_version_id"]))
        qs = db.scalars(select(question_models.Question).order_by(question_models.Question.question_order)).all()
        assert test.total_marks == Decimal("6.50")
        assert [(q.marks, q.negative_marks) for q in qs] == [(Decimal("1.25"), Decimal("0.25")), (Decimal("1.25"), Decimal("0.5")), (4, 1)]


@pytest.mark.parametrize("bad", ["0.255", -1, "NaN", True, "Infinity", 10000])
@pytest.mark.parametrize("field", ["marks", "negative_marks"])
def test_delivery_rejects_bad_marks_and_stores_nothing(env, field, bad):
    c, Session = env
    qs = questions((4, 1))
    qs[0][field] = bad
    assert c.post("/internal/v1/exams", json=payload(questions=qs), headers=H).status_code == 422
    with Session() as db:
        assert db.scalar(select(test_models.Test)) is None


def test_paper_choice_decimal_totals_are_checked_exactly_and_summed_exactly(env, monkeypatch):
    from app.services import paper_eligibility, test_service
    from tests.test_global_exams import PA, PB, PC, q, payload as global_payload, _row
    c, Session = env
    body = global_payload(questions=[q(1, "Physics", PA, "1.25"), q(2, "Physics", PA, 0.5), q(3, "Physics", PB, 4), q(4, "Chemistry", PC, "0.1")])
    papers = {p["paper_id"]: p for s in body["sections"] for p in s["papers"]}
    papers[PA].update(total_marks="1.75")
    papers[PC].update(total_marks="0.1", question_count=1)
    good = {**body, "exam_version_id": str(uuid.uuid4())}
    assert c.post("/internal/v1/exams", json=good, headers=H).status_code == 201, "decimal paper totals that match their questions are accepted"
    bad = global_payload(questions=[q(1, "Physics", PA, "1.25"), q(2, "Physics", PA, 0.5), q(3, "Physics", PB, 4), q(4, "Chemistry", PC, 4)])
    assert c.post("/internal/v1/exams", json=bad, headers=H).status_code == 422       # PA says 8, its questions add up to 1.75
    monkeypatch.setattr(paper_eligibility, "snapshot", lambda *a: None)
    with Session() as db:
        t = db.get(test_models.Test, uuid.UUID(good["exam_version_id"]))
        view = test_service.personalize(db, test_service.format_test(_row(t.test_id, t.sections), db), "STU3")   # STU3: Physics B only
        assert view["total_marks"] == Decimal("4")
        assert t.total_marks == Decimal("5.85")                                                                  # 1.75 + 4 + 0.1, exactly
