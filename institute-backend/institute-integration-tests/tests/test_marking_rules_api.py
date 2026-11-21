"""Test-configured marking rules (marking mode RULES) end to end: plan validation, the snapshot frozen into a generated exam, exact
decimal marks delivered to the Student services, questions and papers without marks of their own, and decimal manual corrections.
PER_QUESTION and UNIFORM behaviour is covered by the existing suites (test_test_creation, test_exams, test_result_corrections)."""
from decimal import Decimal
from uuid import UUID

import pytest

from inst_exam.services import outbox
from tests.test_exams import make_paper
from tests.test_result_corrections import KEY, adjust, corrections, fakes, super_admin  # noqa: F401  (fixtures)
from tests.test_student_integration import FakeAttempts, attempt, live_exam  # noqa: F401  (fixtures)
from tests.test_exams import create_plan, ctx, pa  # noqa: F401  (fixtures)
from tests.test_test_creation import body, generate, tc  # noqa: F401  (fixture)
from tests.test_create_test import ct  # noqa: F401  (fixture)

API = "/api/v1"


def rules(tc, general=("1.25", "0.25"), chemistry=("2.5", "0.5")) -> dict:
    """A general rule for every subject plus a Chemistry-only rule (decimal marks)."""
    items = [{"question_type": "MCQ_SINGLE", "correct": general[0], "wrong": general[1]}]
    if chemistry:
        items.append({"subject_id": str(tc["chem"]), "question_type": "MCQ_SINGLE", "correct": chemistry[0], "wrong": chemistry[1]})
    return {"rules": items}


def rules_plan(client, world, tc, h, **over):
    over = {"marking_mode": "RULES", "marking_rules": rules(tc), **over}
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, **over), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ freezing and delivery

def test_decimal_rules_are_frozen_into_the_exam_and_delivered_exactly(client, world, tc, login, db_factory):
    h = login(tc["pa"])
    plan = rules_plan(client, world, tc, h)
    assert plan["marking_mode"] == "RULES" and plan["marks_correct"] is None
    assert plan["marking_rules"]["rules"][0] == {"subject_id": None, "question_type": "MCQ_SINGLE", "correct": "1.25", "wrong": "0.25"}
    job = generate(client, h, plan["plan_id"])
    exam = client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()
    # Physics: 3 x 1.25 (general rule); Chemistry: 2 x 2.5 (its own rule). The questions' own marks (4 and 3) are ignored.
    assert exam["total_marks"] == "8.75"
    assert exam["settings"]["marking"]["mode"] == "RULES" and exam["settings"]["marking"]["rules"] == plan["marking_rules"]["rules"]
    with db_factory() as db:
        payload = outbox.build_payload(db, UUID(exam["exam_version_id"]))
    by_subject = {(q["subject"], q["marks"], q["negative_marks"]) for q in payload["questions"]}
    assert by_subject == {("Physics", "1.25", "0.25"), ("Chemistry", "2.5", "0.5")}      # exact decimal strings, never floats
    assert payload["total_marks"] == "8.75"

    # editing the plan afterwards changes future exams only: the generated exam keeps its frozen rules
    r = client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"marking_rules": rules(tc, general=("4", "1"), chemistry=None)}, headers=h)
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()["settings"]["marking"]["rules"] == plan["marking_rules"]["rules"]
    with db_factory() as db:
        assert outbox.build_payload(db, UUID(exam["exam_version_id"]))["total_marks"] == "8.75"


def test_whole_number_rules_are_delivered_as_integers_like_before(client, world, tc, login, db_factory):
    h = login(tc["pa"])
    plan = rules_plan(client, world, tc, h, marking_rules=rules(tc, general=("4", "1"), chemistry=None))
    exam = client.get(f"{API}/exams/{generate(client, h, plan['plan_id'])['exam_id']}", headers=h).json()
    with db_factory() as db:
        payload = outbox.build_payload(db, UUID(exam["exam_version_id"]))
    assert {(q["marks"], q["negative_marks"]) for q in payload["questions"]} == {(4, 1)} and payload["total_marks"] == 20


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("over, code", [
    ({"marking_mode": "RULES"}, None),                                                                        # RULES without rules
    ({"marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1, "marking_rules": {"rules": [
        {"question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1}]}}, None),                                  # rules with another mode
    ({"marking_mode": "RULES", "marking_rules": {"rules": [{"question_type": "MCQ_SINGLE", "correct": "0.255", "wrong": 0}]}}, None),
    ({"marking_mode": "RULES", "marking_rules": {"rules": [{"question_type": "MCQ_SINGLE", "correct": "-1", "wrong": 0}]}}, None),
    ({"marking_mode": "RULES", "marking_rules": {"rules": [{"question_type": "MSQ", "correct": 4, "wrong": 1}]}}, None),
    ({"marking_mode": "RULES", "marking_rules": {"rules": [{"question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1, "unanswered": 1}]}}, None),
    ({"marking_mode": "UNIFORM", "marks_correct": 1.5, "marks_wrong": 1}, None),                              # UNIFORM stays whole numbers
])
def test_invalid_marking_is_rejected(client, world, tc, login, over, code):
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, **over), headers=login(tc["pa"]))
    assert r.status_code == 422, r.text


def test_rules_must_cover_exactly_the_plans_subjects(client, world, tc, login):
    h = login(tc["pa"])
    only_physics = {"rules": [{"subject_id": str(tc["phy"]), "question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1}]}
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, marking_mode="RULES", marking_rules=only_physics), headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_MARKING_RULES"            # Chemistry would have no marks
    foreign = {"rules": [{"question_type": "MCQ_SINGLE", "correct": 4, "wrong": 1},
                         {"subject_id": "00000000-0000-0000-0000-000000000001", "question_type": "MCQ_SINGLE", "correct": 1, "wrong": 0}]}
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, marking_mode="RULES", marking_rules=foreign), headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_MARKING_RULES"
    # the dry run applies the same check
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans/check", json=body(tc, marking_mode="RULES", marking_rules=only_physics), headers=h)
    assert r.status_code == 422


def test_switching_away_from_rules_clears_them(client, world, tc, login):
    h = login(tc["pa"])
    plan = rules_plan(client, world, tc, h)
    r = client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1}, headers=h)
    assert r.status_code == 200 and r.json()["marking_rules"] is None and r.json()["marks_correct"] == 4
    r = client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"marking_mode": "RULES"}, headers=h)
    assert r.status_code == 422                                                                         # RULES needs rules again


def test_another_provider_cannot_change_a_plans_marking(client, world, tc, login):
    plan = rules_plan(client, world, tc, login(tc["pa"]))
    r = client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"marking_rules": rules(tc, chemistry=None)}, headers=login(tc["pa2"]))
    assert r.status_code in (403, 404)


# ------------------------------------------------------------------ papers without marks of their own

@pytest.fixture()
def unmarked(world, tc, db_factory):
    _paper, version = make_paper(db_factory, world["p1"], world["p1b1"], tc["phy"], "Imported Physics", "PUBLISHED", n=2, marks=None)
    return version


def test_per_question_marking_refuses_a_paper_without_marks(client, world, tc, login, unmarked):
    h = login(tc["pa"])
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans",
                       json=body(tc, slots=[{"subject_id": str(tc["phy"]), "paper_version_id": str(unmarked)}]), headers=h).json()
    job = generate(client, h, plan["plan_id"])
    assert job["status"] == "FAILED" and "without marks" in job["error"] and job["exam_id"] is None     # never guessed


def test_rules_and_uniform_marking_mark_a_paper_without_marks(client, world, tc, login, unmarked, db_factory):
    h = login(tc["pa"])
    slots = [{"subject_id": str(tc["phy"]), "paper_version_id": str(unmarked)}]
    plan = rules_plan(client, world, tc, h, slots=slots, marking_rules=rules(tc, general=("0.5", "0.25"), chemistry=None))
    exam = client.get(f"{API}/exams/{generate(client, h, plan['plan_id'])['exam_id']}", headers=h).json()
    assert exam["total_marks"] == "1.00"
    with db_factory() as db:
        assert {(q["marks"], q["negative_marks"]) for q in outbox.build_payload(db, UUID(exam["exam_version_id"]))["questions"]} == {("0.5", "0.25")}
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans",
                       json=body(tc, slots=slots, marking_mode="UNIFORM", marks_correct=4, marks_wrong=1), headers=h).json()
    exam = client.get(f"{API}/exams/{generate(client, h, plan['plan_id'])['exam_id']}", headers=h).json()
    assert exam["total_marks"] == "8.00"


# ------------------------------------------------------------------ question bank: marks are optional

def question(tc, world, **over):
    q = {"branch_id": str(world["p1b1"]), "subject_id": str(tc["phy"]), "stem": "What is the SI unit of force?", "option_a": "Newton",
         "option_b": "Joule", "option_c": "Watt", "option_d": "Pascal", "correct_option": "A"}
    q.update(over)
    return q


def test_questions_need_no_marks_and_papers_built_from_them_have_no_total(client, world, tc, login):
    h = login(tc["pa"])
    r = client.post(f"{API}/questions", json=question(tc, world), headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["marks_positive"] is None and r.json()["marks_negative"] is None
    built = client.post(f"{API}/papers", json={"scope": "PROVIDER", "branch_id": str(world["p1b1"]), "subject_id": str(tc["phy"]),
                                               "title": "Built without marks", "question_ids": [r.json()["question_id"]]}, headers=h)
    assert built.status_code == 201, built.text
    assert built.json()["total_marks"] is None


def test_question_marks_are_both_or_neither_with_two_decimals(client, world, tc, login):
    h = login(tc["pa"])
    r = client.post(f"{API}/questions", json=question(tc, world, stem="Only positive marks given?", marks_positive="2.5"), headers=h)
    assert r.status_code == 201                         # earlier behaviour: no deduction unless given (decimals compared exactly)
    assert Decimal(r.json()["marks_positive"]) == Decimal("2.5") and Decimal(r.json()["marks_negative"]) == 0
    assert client.post(f"{API}/questions", json=question(tc, world, stem="Only a deduction given?", marks_negative=1), headers=h).status_code == 422
    assert client.post(f"{API}/questions", json=question(tc, world, stem="Three decimal places?", marks_positive="1.255"), headers=h).status_code == 422


# ------------------------------------------------------------------ manual corrections with decimals

def test_decimal_adjustment_is_exact_end_to_end(client, live_exam, fakes, super_admin, db_factory):
    results, _ = fakes
    r = adjust(client, live_exam["exam_id"], "STU000001", "8.75", super_admin["h"]())
    assert r.status_code == 200, r.text
    assert r.json()["new_marks"] == 8.75 and r.json()["previous_marks"] == 7
    assert results.calls[0][3] == "8.75"                                    # sent as an exact decimal string, never a float
    (c,) = corrections(db_factory)
    assert c.request == {"student_id": "STU000001", "obtained_marks": "8.75"}
    for bad in ("8.755", "NaN", "Infinity"):
        r = adjust(client, live_exam["exam_id"], "STU000002", bad, super_admin["h"](), key=f"adj-bad-{bad}")
        assert r.status_code == 422


# ------------------------------------------------------------------ Create Test and Weekly Test accept RULES too

def test_create_test_and_weekly_test_validate_and_store_rules(client, world, ct, login, db_factory):  # noqa: F811
    from tests.test_create_test import draft
    from tests.test_weekly_tests import create, plans, series
    h = login(ct["pa"])
    good = {"rules": [{"subject_id": str(ct["phy"]), "question_type": "MCQ_SINGLE", "correct": "1.5", "wrong": "0.5"}]}
    other = {"rules": [{"subject_id": str(ct["chem"]), "question_type": "MCQ_SINGLE", "correct": "1.5", "wrong": "0.5"}]}
    no_marks = {"marking_mode": "RULES", "marks_correct": None, "marks_wrong": None}
    assert client.post(f"{API}/tests/check", json=draft(world, ct, **no_marks, marking_rules=good), headers=h).status_code == 200
    # a rule for a subject the test does not have, and nothing for Physics: refused before anything is created
    assert client.post(f"{API}/tests/check", json=draft(world, ct, **no_marks, marking_rules=other), headers=h).status_code == 422
    r = create(client, h, series(world, ct, **no_marks, marking_rules=good))
    assert r.status_code == 201, r.text
    assert r.json()["marking_rules"]["rules"][0]["correct"] == "1.5"
    rows = plans(db_factory, r.json()["series_id"])
    assert rows and all(p.marking_mode == "RULES" and p.marking_rules == r.json()["marking_rules"] for p in rows)   # copied to every date
    assert create(client, h, series(world, ct, **no_marks, marking_rules=other), key="weekly-request-0002").status_code == 422
