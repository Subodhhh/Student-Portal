"""Shuffle Questions (Yes/No) on Daily and Weekly Tests: saved with the test, frozen into the generated exam's rules, and delivered to the
Student services, which give each student their own fixed order. The paper, its questions, answers and marks are never reordered or
changed here; No (the default, and every existing test) keeps today's behaviour exactly."""
from datetime import timedelta
from uuid import UUID

from inst_exam.services import exam_generation as gen
from inst_exam.services import outbox
from tests.test_create_test import API, ct, draft, publish, ready_paper  # noqa: F401  (fixtures and helpers)
from tests.test_test_creation import tc  # noqa: F401  (fixture)
from tests.test_weekly_tests import create, plans, series


def _exam_of(db_factory, plan_id):
    from institute_common.models.exams import Exam, ExamPlan, ExamVersion
    with db_factory() as db:
        plan = db.get(ExamPlan, UUID(str(plan_id)))
        exam = db.query(Exam).filter_by(plan_id=plan.plan_id).one()
        ev = db.query(ExamVersion).filter_by(exam_id=exam.exam_id).one()
        return plan.shuffle_questions, ev.settings, outbox.build_payload(db, ev.exam_version_id)


def test_a_daily_test_saves_and_delivers_yes_and_keeps_no_as_today(client, world, ct, login, db_factory):  # noqa: F811
    pv = ready_paper(client, login, world, ct)
    h = login(ct["pa"])                                   # after ready_paper, which signs in again
    on = publish(client, h, draft(world, ct, subjects=(("phy", pv),), shuffle_questions=True))
    off = publish(client, h, draft(world, ct, subjects=(("phy", pv),), name="Physics again"))     # the field left out: No
    assert on.status_code == off.status_code == 201, (on.text, off.text)
    assert (on.json()["shuffle_questions"], off.json()["shuffle_questions"]) == (True, False)
    saved, settings, payload = _exam_of(db_factory, on.json()["plan_id"])
    assert saved is True and settings["shuffle_questions"] is True and settings["policies"]["shuffle"] == "PER_STUDENT"
    assert payload["shuffle_questions"] is True
    assert [q["order"] for q in payload["questions"]] == [1, 2, 3]                       # the paper order is what is delivered
    assert {(q["marks"], q["negative_marks"]) for q in payload["questions"]} == {(4, 1)}  # marking unchanged
    saved, settings, payload = _exam_of(db_factory, off.json()["plan_id"])
    assert saved is False and settings["shuffle_questions"] is False and settings["policies"] == gen.FIXED_POLICIES
    assert "shuffle_questions" not in payload                                            # an unshuffled exam's payload is unchanged
    listed = {x["plan_id"]: x["shuffle_questions"] for x in client.get(f"{API}/tests", headers=h).json()["items"]}
    assert listed[on.json()["plan_id"]] is True and listed[off.json()["plan_id"]] is False
    detail = client.get(f"{API}/exams/{on.json()['exam_id']}", headers=h).json()
    assert detail["settings"]["policies"]["shuffle"] == "PER_STUDENT"                     # the exam page states the rule


def test_shuffle_must_be_a_yes_or_no(client, world, ct, login):  # noqa: F811
    r = client.post(f"{API}/tests/check", json=draft(world, ct, shuffle_questions="sometimes"), headers=login(ct["pa"]))
    assert r.status_code == 422


def test_a_weekly_test_saves_yes_on_the_series_and_every_occurrence(client, world, ct, login, db_factory):  # noqa: F811
    h = login(ct["pa"])
    s = create(client, h, series(world, ct, shuffle_questions=True))
    assert s.status_code == 201, s.text
    sid = s.json()["series_id"]
    assert s.json()["shuffle_questions"] is True
    assert client.get(f"{API}/test-series/{sid}", headers=h).json()["shuffle_questions"] is True      # retrieved when viewing it
    occurrences = plans(db_factory, sid)
    assert occurrences and all(p.shuffle_questions for p in occurrences)
    with db_factory() as db:
        gen.run_due_plans(db, now=occurrences[0].generate_at + timedelta(minutes=1))
    _saved, settings, payload = _exam_of(db_factory, occurrences[0].plan_id)
    assert settings["policies"]["shuffle"] == "PER_STUDENT" and payload["shuffle_questions"] is True
    plain = create(client, h, series(world, ct, name="Sunday Physics", weekdays=[6]), key="weekly-request-0002").json()
    assert plain["shuffle_questions"] is False and not any(p.shuffle_questions for p in plans(db_factory, plain["series_id"]))
