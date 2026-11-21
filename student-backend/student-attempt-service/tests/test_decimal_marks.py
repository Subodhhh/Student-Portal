"""The attempt service only echoes question marks: whole marks stay JSON integers, fractions are exact JSON numbers.

Run with: DATABASE_URL=sqlite:// pytest tests/test_decimal_marks.py"""
import uuid
from decimal import Decimal

from app.schemas.attempt import WorkspaceQuestion


def question(marks, negative):
    return WorkspaceQuestion(question_id=uuid.uuid4(), question_order=1, subject=None, question_text="Q", question_type="MCQ",
                                   option_a="a", option_b="b", option_c="c", option_d="d", marks=marks, negative_marks=negative,
                                   image_reference=None, selected_answer=None, question_state="NOT_VISITED")


def test_whole_marks_stay_integers_and_fractions_are_exact():
    whole = question(Decimal("4.00"), Decimal("1.00")).model_dump_json()
    assert '"marks":4,' in whole and '"negative_marks":1,' in whole
    frac = question(Decimal("1.25"), Decimal("0.25")).model_dump_json()
    assert '"marks":1.25,' in frac and '"negative_marks":0.25,' in frac
