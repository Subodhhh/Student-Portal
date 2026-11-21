"""Test-configured marking rules (marking mode RULES).

Marks belong to the TEST, not to the question: a test creator enters, for each question type, the marks for a correct answer and
the marks deducted for a wrong answer. A rule may apply to every subject of the test (subject_id = None) or to one subject only;
a subject rule overrides the general rule for that subject. There are no presets and no default values: every number comes from
the test creator. Unanswered questions always score 0 (the Student result service's fixed policy), so it is not a rule field.

Marks are exact decimals with at most 2 decimal places (0.25, 1.5, 4 ...). They are only ever handled as `Decimal`, never float,
and stored in JSON as strings ("0.25") so no precision is lost.

Stored shape (exam_plans / test_series / global_tests `marking_rules`, and frozen in exam_versions.settings.marking):

    {"format": 1, "rules": [{"subject_id": null, "question_type": "MCQ_SINGLE", "correct": "4", "wrong": "1"},
                            {"subject_id": "<uuid>", "question_type": "MCQ_SINGLE", "correct": "1.25", "wrong": "0.25"}]}

`format` lets later question types add fields without breaking rules already stored.

Question types (core/question_types.py): MCQ_SINGLE, MCQ_MULTI (MSQ) and NUMERICAL (NAT). MSQ is scored all-or-nothing and never
negative, so an MSQ rule's `wrong` must be 0. Frozen exam markings also record each type's scoring scheme (`schemes`), so a later
scheme can never change an exam already generated.
"""
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from institute_common.core.taxonomy import QUESTION_TYPES
from institute_common.util import Strict

RULES_FORMAT = 1
MAX_RULES = 50

# Positive marks for a correct answer: more than 0, at most 100, at most 2 decimal places (fits NUMERIC(6,2) everywhere).
CorrectMarks = Annotated[Decimal, Field(gt=0, le=100, decimal_places=2, allow_inf_nan=False)]
# Marks deducted for a wrong answer, written as a positive number (0.25 means "minus 0.25"); 0 means no negative marking.
WrongMarks = Annotated[Decimal, Field(ge=0, le=100, decimal_places=2, allow_inf_nan=False)]

QuestionType = Literal[QUESTION_TYPES]


class MarkingRuleMissing(Exception):
    """A question's (subject, question type) has no marking rule. Generation stops instead of guessing marks."""


class MarkingRuleIn(Strict):
    """One rule: the marks of one question type, for every subject (subject_id None) or for one subject."""
    subject_id: UUID | None = None
    question_type: QuestionType
    correct: CorrectMarks
    wrong: WrongMarks


class MarkingRulesIn(Strict):
    """The complete marking configuration a test creator submits for a RULES test."""
    format: Literal[1] = RULES_FORMAT
    rules: list[MarkingRuleIn] = Field(min_length=1, max_length=MAX_RULES)

    @model_validator(mode="after")
    def msq_never_negative(self):
        # MSQ is all-or-nothing: a wrong set scores 0, never negative marks (decision R5)
        for rule in self.rules:
            if rule.question_type == "MCQ_MULTI" and rule.wrong != 0:
                raise ValueError("MSQ questions are scored all-or-nothing and never lose marks: their wrong mark must be 0")
        return self

    @model_validator(mode="after")
    def one_rule_per_case(self):
        # Two rules for the same (subject, question type) would make the marks ambiguous.
        seen = set()
        for rule in self.rules:
            key = (rule.subject_id, rule.question_type)
            if key in seen:
                raise ValueError("two marking rules cover the same subject and question type")
            seen.add(key)
        return self

    def to_storage(self) -> dict:
        """JSON-safe form for the database: Decimals become exact strings, UUIDs strings."""
        return self.model_dump(mode="json")


def check_marking_choice(marking_mode: str, marks_correct, marks_wrong, marking_rules) -> None:
    """Request-body check shared by every API that configures marking. Only the RULES combinations are checked here; each API keeps
    its existing PER_QUESTION / UNIFORM checks unchanged. Raises ValueError (Pydantic turns it into a 422)."""
    if marking_mode == "RULES":
        if marking_rules is None:
            raise ValueError("rules-based marking needs marking_rules")
        if marks_correct is not None or marks_wrong is not None:
            raise ValueError("marks_correct/marks_wrong apply only to UNIFORM marking")
    elif marking_rules is not None:
        raise ValueError("marking_rules apply only to RULES marking")


def unknown_subjects(stored_rules: dict, subject_ids) -> list[str]:
    """Subject ids named by rules that are not subjects of the test (a typo or another test's subject)."""
    allowed = {str(s) for s in subject_ids}
    return [r["subject_id"] for r in stored_rules["rules"] if r["subject_id"] is not None and r["subject_id"] not in allowed]


def find_rule(stored_rules: dict, subject_id, question_type: str) -> tuple[Decimal, Decimal] | None:
    """(correct, wrong) for one question: the subject's own rule first, otherwise the rule for every subject; None when neither."""
    general = None
    for rule in stored_rules["rules"]:
        if rule["question_type"] != question_type:
            continue
        marks = (Decimal(rule["correct"]), Decimal(rule["wrong"]))
        if rule["subject_id"] is not None and rule["subject_id"] == str(subject_id):
            return marks
        if rule["subject_id"] is None:
            general = marks
    return general


def subjects_without_rules(stored_rules: dict, subject_ids, question_types=None) -> list[str]:
    """Subjects that would have no marks for some question type the rules name. Checked when a test is saved, so the creator learns
    about a gap long before generation (which checks again against the question types actually in the papers). Only the types the
    rules mention are required: rules for MCQ alone (every test made before MSQ and NAT existed) behave exactly as before."""
    if question_types is None:
        question_types = sorted({r["question_type"] for r in stored_rules["rules"]}) or ["MCQ_SINGLE"]
    return [str(s) for s in subject_ids if any(find_rule(stored_rules, s, qt) is None for qt in question_types)]


def marking_settings(marking_mode: str, marks_correct, marks_wrong, marking_rules: dict | None) -> dict:
    """The marking of a test as frozen into a generated exam (exam_versions.settings["marking"]) and as shown in test summaries.
    PER_QUESTION and UNIFORM keep exactly the shape they always had; RULES adds the format and the rules (exact decimal strings)."""
    if marking_mode == "UNIFORM":
        return {"mode": "UNIFORM", "correct": marks_correct, "wrong": marks_wrong}
    if marking_mode == "RULES":
        return {"mode": "RULES", "format": marking_rules["format"], "rules": marking_rules["rules"]}
    return {"mode": marking_mode}


def with_schemes(marking: dict, question_types) -> dict:
    """The frozen marking of an exam, plus the scoring scheme of every non-MCQ type it contains (MSQ ALL_OR_NOTHING, NAT
    NUMERIC_TOLERANCE). An exam of MCQ questions only keeps exactly the shape it always had."""
    from institute_common.core.question_types import MCQ, SCHEMES
    others = sorted({t for t in question_types if t != MCQ})
    return {**marking, "schemes": {t: SCHEMES[t] for t in others}} if others else marking


def question_marks_for(marking: dict, subject_id, question_type: str, own_positive, own_negative) -> tuple[Decimal, Decimal]:
    """(marks, negative) of one question under a frozen marking. MSQ never loses marks in any mode (R5): its negative is always 0.
    Raises MarkingRuleMissing / ValueError like the callers expect."""
    mode = marking.get("mode", "PER_QUESTION")
    if mode == "UNIFORM":
        marks, negative = Decimal(str(marking["correct"])), Decimal(str(marking["wrong"]))
    elif mode == "RULES":
        found = find_rule(marking, subject_id, question_type)
        if found is None:
            raise MarkingRuleMissing(f"No marking rule covers a {question_type} question of this exam.")
        marks, negative = found
    else:
        if own_positive is None or own_negative is None:
            raise ValueError("A question has no marks of its own, but this exam uses each question's own marks.")
        marks, negative = Decimal(own_positive), Decimal(own_negative)
    return (marks, Decimal(0)) if question_type == "MCQ_MULTI" else (marks, negative)


def rules_problem(stored_rules: dict | None, subject_ids) -> str | None:
    """Why the rules cannot be saved for a test with these subjects, or None when they fit. Every API that saves rules calls this
    once the test's subjects are known, so the check is identical everywhere."""
    if stored_rules is None:
        return None
    if unknown_subjects(stored_rules, subject_ids):
        return "A marking rule names a subject that is not part of this test."
    if subjects_without_rules(stored_rules, subject_ids):
        return "Every subject of the test needs marks for every question type: add a rule for all subjects or for the missing subject."
    return None


def paper_total(stored_rules: dict, subject_id, question_type_counts: dict[str, int]) -> Decimal:
    """Maximum marks of one paper under the rules: questions of each type x that type's correct marks.
    Raises MarkingRuleMissing when a question type in the paper has no rule."""
    total = Decimal(0)
    for question_type, count in question_type_counts.items():
        marks = find_rule(stored_rules, subject_id, question_type)
        if marks is None:
            raise MarkingRuleMissing(f"No marking rule covers {question_type} questions of a subject in this test.")
        total += marks[0] * count
    return total


def wire_mark(value: Decimal) -> int | str:
    """How a mark is sent to the Student services. Whole marks stay JSON integers, exactly as before decimals existed, so payloads
    of existing exams do not change; fractional marks travel as exact decimal strings ("0.25") because a JSON float could lose
    precision. The Student services accept both forms."""
    value = Decimal(value)
    if value == value.to_integral_value():
        return int(value)
    return format(value.normalize(), "f")


def json_safe(value):
    """A copy of a JSON-like value (dicts, lists, numbers ...) with every Decimal in its exact wire form (see wire_mark), so data read
    from the result service (whose fractional marks are parsed as Decimal) can be stored in a JSON column without float rounding."""
    if isinstance(value, Decimal):
        return wire_mark(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value
