"""Unit tests of institute_common.core.marking: validation of test-configured marking rules, rule lookup, paper totals and the
wire format of marks sent to the Student services (exact decimals, never float)."""
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from institute_common.core.marking import (
    MarkingRuleMissing, MarkingRulesIn, check_marking_choice, find_rule, paper_total, subjects_without_rules, unknown_subjects,
    wire_mark,
)

PHYSICS, CHEMISTRY = uuid4(), uuid4()


def rules(*items) -> dict:
    return MarkingRulesIn(rules=list(items)).to_storage()


def test_decimal_marks_are_kept_exactly_and_stored_as_strings():
    stored = rules({"question_type": "MCQ_SINGLE", "correct": "1.25", "wrong": 0.25})
    assert stored == {"format": 1, "rules": [{"subject_id": None, "question_type": "MCQ_SINGLE", "correct": "1.25", "wrong": "0.25"}]}
    assert find_rule(stored, PHYSICS, "MCQ_SINGLE") == (Decimal("1.25"), Decimal("0.25"))


@pytest.mark.parametrize("correct, wrong", [("0.255", "0"), ("0", "0"), ("-1", "0"), ("4", "-0.5"), ("101", "0"), ("NaN", "0"),
                                            ("Infinity", "0"), ("4", "1.001")])
def test_invalid_marks_are_rejected(correct, wrong):
    with pytest.raises(ValidationError):
        MarkingRulesIn(rules=[{"question_type": "MCQ_SINGLE", "correct": correct, "wrong": wrong}])


def test_unknown_question_type_extra_fields_duplicates_and_empty_rules_are_rejected():
    for bad in ([{"question_type": "MSQ", "correct": 4, "wrong": 1}],                                   # not supported yet
                [{"question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1, "unanswered": 1}],           # unanswered is always 0
                [{"question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1}] * 2,                        # ambiguous
                []):
        with pytest.raises(ValidationError):
            MarkingRulesIn(rules=bad)


def test_subject_rule_overrides_the_general_rule():
    stored = rules({"question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1},
                   {"subject_id": str(CHEMISTRY), "question_type": "MCQ_SINGLE", "correct": "2.5", "wrong": "0.5"})
    assert find_rule(stored, PHYSICS, "MCQ_SINGLE") == (Decimal(4), Decimal(1))
    assert find_rule(stored, CHEMISTRY, "MCQ_SINGLE") == (Decimal("2.5"), Decimal("0.5"))


def test_gaps_and_foreign_subjects_are_reported():
    stored = rules({"subject_id": str(PHYSICS), "question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1})
    assert subjects_without_rules(stored, [PHYSICS, CHEMISTRY]) == [str(CHEMISTRY)]
    assert unknown_subjects(stored, [CHEMISTRY]) == [str(PHYSICS)]
    assert find_rule(stored, CHEMISTRY, "MCQ_SINGLE") is None


def test_paper_total_is_exact_and_refuses_a_missing_rule():
    stored = rules({"question_type": "MCQ_SINGLE", "correct": "1.25", "wrong": "0.25"})
    assert paper_total(stored, PHYSICS, {"MCQ_SINGLE": 3}) == Decimal("3.75")
    # 0.1-style values that float cannot represent stay exact
    stored = rules({"question_type": "MCQ_SINGLE", "correct": "0.1", "wrong": 0})
    assert paper_total(stored, PHYSICS, {"MCQ_SINGLE": 3}) == Decimal("0.3")
    with pytest.raises(MarkingRuleMissing):
        paper_total(rules({"subject_id": str(CHEMISTRY), "question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1}), PHYSICS, {"MCQ_SINGLE": 1})


def test_wire_mark_keeps_whole_numbers_as_integers_and_fractions_as_exact_strings():
    assert wire_mark(Decimal("4")) == 4 and isinstance(wire_mark(Decimal("4.00")), int)
    assert wire_mark(Decimal("0")) == 0
    assert wire_mark(Decimal("0.25")) == "0.25"
    assert wire_mark(Decimal("1.50")) == "1.5"
    assert wire_mark(Decimal("10.00")) == 10


def test_marking_choice_combinations():
    stored = MarkingRulesIn(rules=[{"question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1}])
    check_marking_choice("RULES", None, None, stored)
    check_marking_choice("UNIFORM", 4, 1, None)
    with pytest.raises(ValueError):
        check_marking_choice("RULES", None, None, None)
    with pytest.raises(ValueError):
        check_marking_choice("RULES", 4, 1, stored)
    with pytest.raises(ValueError):
        check_marking_choice("UNIFORM", 4, 1, stored)
