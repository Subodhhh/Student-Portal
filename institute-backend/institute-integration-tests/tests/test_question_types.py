"""The question-type rulebook (institute_common.core.question_types) against the shared golden vectors
(contracts/question-types/golden.json). The Student services run the same file against their copies, so both sides agree."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from institute_common.core import question_types as qt

GOLDEN = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "question-types" / "golden.json").read_text())


@pytest.mark.parametrize("case", GOLDEN["answers"], ids=lambda c: f"{c['type']}:{c['raw']!r}")
def test_answers_are_normalised_or_refused(case):
    if case.get("error"):
        with pytest.raises(qt.QuestionTypeError):
            qt.normalise_answer(case["type"], case["raw"], content=case.get("content"))
    else:
        assert qt.normalise_answer(case["type"], case["raw"], content=case.get("content")) == case["expect"]


@pytest.mark.parametrize("case", GOLDEN["scores"], ids=lambda c: f"{c['type']}:{c['answer']!r}")
def test_scores(case):
    status, marks = qt.score(case["type"], case["key"], case["answer"], Decimal(case["marks"]), Decimal(case["negative"]))
    assert (status, marks) == (case["status"], Decimal(case["marks_obtained"]))


@pytest.mark.parametrize("case", GOLDEN["questions"], ids=lambda c: f"{c['type']}:{json.dumps(c.get('key'))}")
def test_questions_are_validated(case):
    if case["valid"]:
        _, key = qt.validate(case["type"], case.get("content"), case["key"])
        assert key == case["canonical_key"]
    else:
        with pytest.raises(qt.QuestionTypeError) as err:
            qt.validate(case["type"], case.get("content"), case["key"])
        assert err.value.field == case["field"]


def test_the_student_copies_are_identical_to_the_institute_rulebook():
    """The Student services carry the same file (separate codebase, no shared package): a byte-identical copy, checked here."""
    root = Path(__file__).resolve().parents[3]
    source = (root / "institute-backend/institute-common/institute_common/core/question_types.py").read_text()
    for service in ("student-test-service", "student-attempt-service", "student-result-service"):
        copy = root / "student-backend" / service / "app" / "question_types.py"
        assert copy.exists() and copy.read_text() == source, f"{service}/app/question_types.py differs from the Institute rulebook"


def test_marking_rules_for_msq_never_deduct_and_mcq_only_rules_are_unchanged():
    from uuid import uuid4

    from pydantic import ValidationError

    from institute_common.core import marking
    with pytest.raises(ValidationError, match="never lose marks"):
        marking.MarkingRulesIn(rules=[{"question_type": "MCQ_MULTI", "correct": "4", "wrong": "1"}])
    rules = marking.MarkingRulesIn(rules=[{"question_type": "MCQ_SINGLE", "correct": "4", "wrong": "1"},
                                          {"question_type": "MCQ_MULTI", "correct": "4", "wrong": "0"},
                                          {"question_type": "NUMERICAL", "correct": "4", "wrong": "1"}]).to_storage()
    s = uuid4()
    assert marking.rules_problem(rules, [s]) is None
    mcq_only = marking.MarkingRulesIn(rules=[{"question_type": "MCQ_SINGLE", "correct": "4", "wrong": "1"}]).to_storage()
    assert marking.rules_problem(mcq_only, [s]) is None                   # tests made before MSQ/NAT: exactly as before
    gap = {"format": 1, "rules": [{"subject_id": str(s), "question_type": "NUMERICAL", "correct": "4", "wrong": "1"},
                                  {"subject_id": None, "question_type": "MCQ_SINGLE", "correct": "4", "wrong": "1"}]}
    other = uuid4()
    assert marking.subjects_without_rules(gap, [s, other]) == [str(other)]   # NAT named for one subject only: the other has a gap

    frozen = marking.marking_settings("RULES", None, None, rules)
    assert marking.question_marks_for(frozen, s, "NUMERICAL", None, None) == (Decimal(4), Decimal(1))
    for mode in ({"mode": "UNIFORM", "correct": 4, "wrong": 1}, {"mode": "PER_QUESTION"}, frozen):
        assert marking.question_marks_for(mode, s, "MCQ_MULTI", Decimal(3), Decimal(1))[1] == 0      # MSQ: never negative (R5)
    assert marking.with_schemes({"mode": "PER_QUESTION"}, ["MCQ_SINGLE"]) == {"mode": "PER_QUESTION"}   # MCQ exams unchanged
    assert marking.with_schemes({"mode": "PER_QUESTION"}, ["MCQ_SINGLE", "MCQ_MULTI", "NUMERICAL"])["schemes"] == {
        "MCQ_MULTI": "ALL_OR_NOTHING", "NUMERICAL": "NUMERIC_TOLERANCE"}
