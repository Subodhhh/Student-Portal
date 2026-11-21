"""Decimal marks in the result service: exact scoring, adjustment and re-evaluation, whole-number output unchanged.

Run with: DATABASE_URL=sqlite:// pytest tests/test_decimal_marks.py (in-memory, never the real DB)."""
import uuid
import warnings
from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.marks import Marks
from app.services.reevaluation import score
from tests.test_internal_corrections import H, TEST, adjust_body, env, fake_questions, history, marks, reeval_body  # noqa: F401

warnings.filterwarnings("ignore", message="Dialect sqlite.*Decimal")

D = Decimal


def q(selected, correct, m, n):
    # the shape get_questions_with_answers returns (question id and order included)
    return {"question_id": uuid.uuid4(), "question_order": 1, "selected_answer": selected, "correct_answer": correct, "marks": m,
            "negative_marks": n}


def test_three_right_at_1_25_and_two_wrong_at_0_25_is_exactly_3_25():
    qs = [q("A", "A", D("1.25"), D("0.25"))] * 3 + [q("B", "A", D("1.25"), D("0.25"))] * 2
    out = score(qs)
    assert out["obtained_marks"] == D("3.25") and out["total_marks"] == D("6.25")
    assert out["correct_answers"] == 3 and out["incorrect_answers"] == 2


def test_mixed_whole_and_decimal_with_unanswered_and_float_sql_values():
    # SQLite raw rows give floats: they must still be read exactly
    qs = [q("A", "A", 4, 1), q("B", "A", 0.5, 0.25), q(None, "A", 1.25, 0.25), q("A", "A", 0.1, 0.1), q("B", "A", 0.2, 0.1)]
    out = score(qs)
    assert out["obtained_marks"] == D("3.75")        # 4 - 0.25 + 0.1 - 0.1
    assert out["total_marks"] == D("6.05")


def test_whole_scoring_unchanged():
    assert score([q("A", "A", 4, 1), q("B", "A", 4, 1)])["obtained_marks"] == 3


class Box(BaseModel):
    m: Marks


@pytest.mark.parametrize("bad", ["0.255", "NaN", "Infinity", True, None, "x"])
def test_marks_type_rejects(bad):
    with pytest.raises(ValueError):
        Box(m=bad)


def test_marks_type_serialization():
    assert Box(m=D("4.00")).model_dump_json() == '{"m":4}'
    assert Box(m=D("-1.50")).model_dump_json() == '{"m":-1.5}'
    assert Box(m=D("0.25")).model_dump_json() == '{"m":0.25}'


@pytest.mark.parametrize("raw", [3.25, "3.25", D("3.25")])
def test_adjustment_accepts_number_string_decimal_and_stores_exactly(env, raw):
    c, Session, _ = env
    r = c.post("/internal/v1/results/adjust", json=adjust_body(obtained_marks=str(raw) if isinstance(raw, D) else raw), headers=H)
    assert r.status_code == 200 and r.json()["previous"]["obtained_marks"] == 7 and r.json()["new"]["obtained_marks"] == 3.25
    assert marks(Session, "STU000001") == D("3.25")
    assert history(Session)[0].new["obtained_marks"] == 3.25


@pytest.mark.parametrize("bad", ["0.255", "NaN", True, "Infinity", 12.01, -12.01])
def test_adjustment_rejects_bad_marks(env, bad):
    c, Session, _ = env
    assert c.post("/internal/v1/results/adjust", json=adjust_body(obtained_marks=bad), headers=H).status_code == 422
    assert history(Session) == [] and marks(Session, "STU000001") == 7


def test_whole_adjustment_response_is_still_an_integer(env):
    c, _, _ = env
    body = c.post("/internal/v1/results/adjust", json=adjust_body(obtained_marks=9), headers=H).text
    assert '"obtained_marks":9,' in body or '"obtained_marks":9}' in body
    assert "9.0" not in body and "7.0" not in body


def test_reevaluation_with_decimal_marks_is_exact_and_recorded(env, monkeypatch):
    c, Session, _ = env
    third = [("A", "A", D("1.25"), D("0.25"))] * 3 + [("B", "A", D("1.25"), D("0.25"))] * 2
    fake_questions(monkeypatch, {"STU000001": third, "STU000002": [("A", "A", 4, 1)] * 1, "STU000003": [(None, "A", 4, 1)]})
    out = c.post(f"/internal/v1/tests/{TEST}/reevaluate", json=reeval_body(), headers=H).json()
    assert "STU000001" in out["changed"]
    assert marks(Session, "STU000001") == D("3.25")
    h = [x for x in history(Session) if x.student_id == "STU000001"][0]
    assert h.previous["obtained_marks"] == 7 and h.new["obtained_marks"] == 3.25 and h.new["total_marks"] == 6.25


def test_exam_results_endpoint_marks_json(env):
    c, Session, _ = env
    c.post("/internal/v1/results/adjust", json=adjust_body(obtained_marks="2.5"), headers=H)
    text = c.get(f"/internal/v1/tests/{TEST}/results", headers=H).text
    assert '"obtained_marks":2.5' in text and '"total_marks":12,' in text
