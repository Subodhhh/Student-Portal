"""Failure, retry and concurrency paths of unified Global Test generation (services/global_generation.py): a group that fails and is
retried, enrollment changes between attempts, the MAX_ATTEMPTS stop with its notifications, and two workers on the same date. World:
tests.test_global_unified.uni (STU000001: p1 Physics + Chemistry and p2 Biology; STU000002: p1 Chemistry; STU000004: p2 Biology)."""
import os
import threading
from datetime import timedelta
from uuid import UUID

import pytest

from inst_exam.services import exam_generation, global_generation as gg
from tests.test_global_contributions import client, login  # noqa: F401
from tests.test_global_schedule import gs  # noqa: F401
from tests.test_global_unified import counts, deliver, exams_by_student, uni  # noqa: F401

API = "/api/v1"


def run(db_factory, test, occ):
    from institute_common.models.exams import GlobalTest, GlobalTestOccurrence
    with db_factory() as db:
        o, t = db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"])), db.get(GlobalTest, UUID(test["test_id"]))
        return gg.run_due(db, now=gg.deadline(t, o) + timedelta(minutes=1))


def occurrence(db_factory, occ):
    from institute_common.models.exams import GlobalTestOccurrence
    with db_factory() as db:
        o = db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"]))
        return o.generated_at, o.generation_error


def failing_for(monkeypatch, student, times=None, how="raise"):
    """Make generation of the plan that lists `student` fail: raise from gen.generate (how='raise', `times` calls; None = always), or
    leave a FAILED job (how='job': the generator's own audience step raises, so the job records the failure)."""
    from institute_common.models.exams import ExamPlanStudent
    real_generate, real_audience, left = exam_generation.generate, exam_generation.eligible_students, {"n": times}

    def lists(db, plan_id):
        return bool(db.query(ExamPlanStudent).filter_by(plan_id=plan_id, student_id=student).first())

    def generate(db, plan_id, *a, **kw):
        if lists(db, plan_id) and (left["n"] is None or left["n"] > 0):
            if left["n"] is not None:
                left["n"] -= 1
            raise RuntimeError("simulated generation failure")
        return real_generate(db, plan_id, *a, **kw)

    def audience(db, plan, report=None):
        if lists(db, plan.plan_id):
            raise RuntimeError("simulated audience failure")
        return real_audience(db, plan, report)
    if how == "raise":
        monkeypatch.setattr(exam_generation, "generate", generate)
    else:
        monkeypatch.setattr(exam_generation, "eligible_students", audience)


def notifications(db_factory, kind=None):
    from institute_common.models.exams import ExamNotificationOutbox
    with db_factory() as db:
        return [o.payload for o in db.query(ExamNotificationOutbox) if kind is None or o.payload["kind"] == kind]


def test_a_failed_group_is_retried_and_nothing_is_duplicated(client, world, gs, uni, db_factory, monkeypatch):  # noqa: F811
    t, occ = uni
    failing_for(monkeypatch, "STU000004", times=1)
    [first] = run(db_factory, t, occ)
    assert first["failed"] and occurrence(db_factory, occ)[0] is None                   # the date stays open for the next pass
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert set(got) == {"STU000001", "STU000002", "STU000003"}                            # the other groups were generated
    notes_after_first = len(notifications(db_factory))
    [second] = run(db_factory, t, occ)
    assert not second["failed"] and occurrence(db_factory, occ) [0] is not None and occurrence(db_factory, occ)[1] is None
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert {s: len(x) for s, x in got.items()} == {"STU000001": 1, "STU000002": 1, "STU000003": 1, "STU000004": 1}
    deliver(db_factory)
    c = counts(db_factory)
    assert (c["ExamPlan"], c["Exam"], c["StudentExamAssignment"], c["ExamOutbox"]) == (4, 4, 4, 4), c
    assert len(notifications(db_factory)) == notes_after_first                           # provider notices are not repeated
    assert run(db_factory, t, occ) == [] and counts(db_factory) == c


def test_enrollment_changes_between_attempts_give_each_student_exactly_one_correct_exam(client, world, gs, uni, db_factory, monkeypatch):  # noqa: F811
    from institute_common.models.org import Enrollment, EnrollmentBatch
    t, occ = uni
    failing_for(monkeypatch, "STU000001", times=1)
    run(db_factory, t, occ)                                                               # STU000001's group fails; STU000002 (Chemistry) is generated
    with db_factory() as db:
        db.query(Enrollment).filter_by(enrollment_id="g5").update({"status": "INACTIVE"})   # STU000001 leaves the tutor (Biology)
        db.add(Enrollment(enrollment_id="g8", provider_id=world["p1"], branch_id=world["p1b1"], student_id="STU000008"))
        db.flush()
        db.add(EnrollmentBatch(enrollment_id="g8", batch_id=gs["batch_b"]))              # a new student with STU000002's papers
        db.commit()
    [second] = run(db_factory, t, occ)
    assert not second["failed"], second
    got = exams_by_student(db_factory, occ["occurrence_id"])
    p1 = world["p1"]
    assert [x[1] for x in got["STU000001"]] == [[(gs["phy"], gs["paperA"], p1), (gs["chem"], gs["paperC"], p1)]]   # Biology gone, one exam
    assert len(got["STU000002"]) == 1 and len(got["STU000008"]) == 1                     # same papers, STU000002 untouched
    assert got["STU000008"][0][1] == got["STU000002"][0][1] and got["STU000008"][0][0] != got["STU000002"][0][0]   # a follow-up exam
    assert all(len(x) == 1 for x in got.values())


def test_three_failed_attempts_stop_the_group_and_tell_the_platform_and_its_providers(client, world, gs, uni, db_factory, monkeypatch):  # noqa: F811
    from institute_common.models.exams import ExamGenerationJob, ExamPlan, ExamPlanStudent
    t, occ = uni
    failing_for(monkeypatch, "STU000004", how="job")                                     # a permanent failure, recorded on the job
    for attempt in (1, 2):
        [r] = run(db_factory, t, occ)
        assert r["failed"] and occurrence(db_factory, occ)[0] is None, (attempt, r)
        assert notifications(db_factory, "GLOBAL_TEST_GENERATION_FAILED") == []
    [third] = run(db_factory, t, occ)
    assert not third["failed"] and third["abandoned"], third
    generated_at, error = occurrence(db_factory, occ)
    assert generated_at is not None and "simulated audience failure" in error             # the date is closed, the cause stays visible
    notes = notifications(db_factory, "GLOBAL_TEST_GENERATION_FAILED")
    platform = {s for n in notes if n["provider_id"] is None for s in n["staff_ids"]}
    with db_factory() as db:
        from institute_common.models.staff import Staff, StaffRoleAssignment
        global_staff = {str(a.staff_id) for a in db.query(StaffRoleAssignment).filter(StaffRoleAssignment.role.in_(("SUPER_ADMIN", "ADMIN")))
                        if db.get(Staff, a.staff_id).status == "ACTIVE"}
        plan = db.query(ExamPlan).join(ExamPlanStudent, ExamPlanStudent.plan_id == ExamPlan.plan_id).filter(
            ExamPlan.global_occurrence_id == UUID(occ["occurrence_id"]), ExamPlanStudent.student_id == "STU000004").one()
        attempts = db.query(ExamGenerationJob).filter_by(plan_id=plan.plan_id).one().attempts
    assert platform == global_staff and attempts == 3
    providers = [n for n in notes if n["provider_id"] is not None]
    assert [n["provider_id"] for n in providers] == [str(world["p2"])]                    # only the group's own provider (Biology: the tutor)
    assert "1 of your students" in providers[0]["body"] and "Provider p1" not in providers[0]["body"] and "simulated" not in providers[0]["body"]
    assert "STU000004" not in exams_by_student(db_factory, occ["occurrence_id"])
    before = (counts(db_factory), len(notifications(db_factory)))
    with db_factory() as db:                                                              # a forced re-run neither retries nor re-notifies
        from institute_common.models.exams import GlobalTestOccurrence
        gg.generate_occurrence(db, db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"])))
        assert db.query(ExamGenerationJob).filter_by(plan_id=plan.plan_id).one().attempts == 3      # an abandoned group is not tried again
    assert (counts(db_factory), len(notifications(db_factory))) == before


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="real concurrency needs PostgreSQL (TEST_DATABASE_URL)")
def test_two_workers_on_the_same_date_generate_it_once(client, world, gs, uni, db_factory):  # noqa: F811
    from institute_common.models.exams import GlobalTest, GlobalTestOccurrence
    t, occ = uni
    barrier, errors = threading.Barrier(2), []

    def worker():
        try:
            with db_factory() as db:
                o, test = db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"])), db.get(GlobalTest, UUID(t["test_id"]))
                when = gg.deadline(test, o) + timedelta(minutes=1)
                barrier.wait()
                gg.run_due(db, now=when)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
    threads = [threading.Thread(target=worker) for _ in range(2)]
    [x.start() for x in threads]
    [x.join() for x in threads]
    assert not errors, errors
    run(db_factory, t, occ)                                                               # a later pass finishes anything a loser left open
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert {s: len(x) for s, x in got.items()} == {"STU000001": 1, "STU000002": 1, "STU000003": 1, "STU000004": 1}, got
    deliver(db_factory)
    c = counts(db_factory)
    assert (c["ExamPlan"], c["Exam"], c["StudentExamAssignment"], c["ExamOutbox"]) == (4, 4, 4, 4), c
    keys = [n["dedupe"] + ",".join(n["staff_ids"]) for n in notifications(db_factory)]
    assert len(keys) == len(set(keys))                                                    # no notification twice
