"""Failure, retry and concurrency paths of Global Test generation (services/global_generation.py; ONE exam per date): a pass that
fails and is retried, the eligibility frozen at the first pass, a student inactive with one provider, the MAX_ATTEMPTS stop with its
notifications and the Super Admin's manual retry, the question limit, the Super Admin's outbox requeue, and two workers on one date.
World: tests.test_global_unified.uni (STU000001: p1 Physics + Chemistry and p2 Biology; STU000002: p1 Chemistry; STU000004: p2 Biology)."""
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


def stopped(db_factory, occ):
    from institute_common.models.exams import GlobalTestOccurrence
    with db_factory() as db:
        o = db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"]))
        return o.generation_failed_at, o.generation_attempts


def retry(client, login, who, occ, key="retry-key-0001"):
    return client.post(f"{API}/global-occurrences/{occ['occurrence_id']}/generation/retry", json={"reason": "Cause fixed by the platform"},
                       headers={**login(who), "Idempotency-Key": key})


def test_a_failed_pass_is_retried_and_nothing_is_duplicated(client, world, gs, uni, db_factory, monkeypatch):  # noqa: F811
    t, occ = uni
    failing_for(monkeypatch, "STU000004", times=1)
    [first] = run(db_factory, t, occ)
    assert first["failed"] and occurrence(db_factory, occ)[0] is None                   # the date stays open for the next pass
    assert exams_by_student(db_factory, occ["occurrence_id"]) == {}                       # the date's one exam: not yet
    notes_after_first = len(notifications(db_factory))
    [second] = run(db_factory, t, occ)
    assert not second["failed"] and occurrence(db_factory, occ)[0] is not None and occurrence(db_factory, occ)[1] is None
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert {s: len(x) for s, x in got.items()} == {"STU000001": 1, "STU000002": 1, "STU000003": 1, "STU000004": 1}
    deliver(db_factory)
    c = counts(db_factory)
    assert (c["ExamPlan"], c["Exam"], c["StudentExamAssignment"], c["ExamOutbox"]) == (1, 1, 4, 1), c
    assert len(notifications(db_factory)) == notes_after_first                           # provider notices are not repeated
    assert run(db_factory, t, occ) == [] and counts(db_factory) == c


def test_the_audience_is_frozen_at_the_first_pass(client, world, gs, uni, db_factory, monkeypatch):  # noqa: F811
    from institute_common.models.exams import GlobalTestStudentSubject
    from institute_common.models.org import Enrollment, EnrollmentBatch
    t, occ = uni
    failing_for(monkeypatch, "STU000001", times=1)
    run(db_factory, t, occ)                                                               # frozen, then the generation fails
    with db_factory() as db:
        db.query(Enrollment).filter_by(enrollment_id="g5").update({"status": "INACTIVE"})   # STU000001 leaves the tutor afterwards
        db.add(Enrollment(enrollment_id="g8", provider_id=world["p1"], branch_id=world["p1b1"], student_id="STU000008"))
        db.flush()
        db.add(EnrollmentBatch(enrollment_id="g8", batch_id=gs["batch_b"]))              # a late joiner
        db.commit()
    [second] = run(db_factory, t, occ)
    assert not second["failed"], second
    got = exams_by_student(db_factory, occ["occurrence_id"])
    assert "STU000008" not in got and all(len(x) == 1 for x in got.values())           # never added to an already-frozen date
    with db_factory() as db:
        cell = db.get(GlobalTestStudentSubject, (UUID(occ["occurrence_id"]), "STU000001", gs["bio"]))
        assert cell.status == "PAPER"                                                     # eligibility as frozen at the deadline


def test_a_student_inactive_with_one_provider_keeps_the_other_providers_papers(client, world, gs, uni, db_factory):  # noqa: F811
    from institute_common.models.exams import GlobalTestStudentSubject
    from institute_common.models.org import Enrollment
    t, occ = uni
    with db_factory() as db:
        db.query(Enrollment).filter_by(enrollment_id="g5").update({"status": "INACTIVE"})   # STU000001 left the tutor before the deadline
        db.commit()
    [r] = run(db_factory, t, occ)
    assert not r["failed"], r
    assert len(exams_by_student(db_factory, occ["occurrence_id"])["STU000001"]) == 1     # still sits the date
    with db_factory() as db:
        status = {c.subject_id: c.status for c in db.query(GlobalTestStudentSubject).filter_by(student_id="STU000001")}
    assert status[gs["phy"]] == status[gs["chem"]] == "PAPER" and status[gs["bio"]] != "PAPER"   # only the tutor's paper is gone


def test_failed_passes_stop_the_date_until_the_super_admin_retries_it(client, world, gs, uni, login, db_factory, monkeypatch):  # noqa: F811
    t, occ = uni
    failing_for(monkeypatch, "STU000004", how="job")                                     # a permanent failure, recorded on the job
    for attempt in (1, 2):
        [r] = run(db_factory, t, occ)
        assert r["failed"] and stopped(db_factory, occ) == (None, attempt), (attempt, r)
        assert notifications(db_factory, "GLOBAL_TEST_GENERATION_FAILED") == []
    [third] = run(db_factory, t, occ)
    failed_at, attempts = stopped(db_factory, occ)
    assert third["failed"] and failed_at is not None and attempts == 3
    assert occurrence(db_factory, occ)[0] is None and "simulated audience failure" in occurrence(db_factory, occ)[1]
    notes = notifications(db_factory, "GLOBAL_TEST_GENERATION_FAILED")
    platform = {s for n in notes if n["provider_id"] is None for s in n["staff_ids"]}
    with db_factory() as db:
        from institute_common.models.staff import Staff, StaffRoleAssignment
        global_staff = {str(a.staff_id) for a in db.query(StaffRoleAssignment).filter(StaffRoleAssignment.role.in_(("SUPER_ADMIN", "ADMIN")))
                        if db.get(Staff, a.staff_id).status == "ACTIVE"}
    assert platform == global_staff
    providers = [n for n in notes if n["provider_id"] is not None]
    assert sorted(n["provider_id"] for n in providers) == sorted([str(world["p1"]), str(world["p2"])])   # every contributor of the date
    assert all("simulated" not in n["body"] and "Provider p" not in n["body"] for n in providers)
    assert run(db_factory, t, occ) == []                                                  # stopped: never retried automatically
    # only the Super Admin retries, once the cause is fixed; repeating it changes nothing
    assert retry(client, login, gs["admin"], occ).status_code == 403
    monkeypatch.undo()
    r = retry(client, login, gs["sa"], occ)
    assert r.status_code == 200 and r.json()["retried"] is True and r.json()["generation_attempts"] == 0, r.text
    assert retry(client, login, gs["sa"], occ, key="retry-key-0002").json()["retried"] is False
    [ok] = run(db_factory, t, occ)
    assert not ok["failed"] and occurrence(db_factory, occ)[0] is not None
    deliver(db_factory)
    c = counts(db_factory)
    assert (c["ExamPlan"], c["Exam"], c["StudentExamAssignment"], c["ExamOutbox"]) == (1, 1, 4, 1), c


def test_the_question_limit_stops_the_date_at_once_and_is_warned_before(client, world, gs, uni, login, db_factory, monkeypatch):  # noqa: F811
    from institute_common.config import settings
    t, occ = uni
    monkeypatch.setattr(settings, "global_test_max_questions", 6)                        # the date's papers hold 7 questions
    page = client.get(f"{API}/global-occurrences/{occ['occurrence_id']}", headers=login(gs["sa"])).json()
    assert page["question_limit"] == {"limit": 6, "exceeded": True, "projected": 7, "mine": None}
    mine = client.get(f"{API}/global-occurrences/{occ['occurrence_id']}", headers=login(gs["tutor"])).json()["question_limit"]
    assert mine == {"limit": 6, "exceeded": True, "projected": None, "mine": 2}           # a provider sees only its own figures
    [r] = run(db_factory, t, occ)
    assert r["failed"] and r["question_limit_exceeded"] and "QUESTION_LIMIT_EXCEEDED" in r["failed"][0]["error"]
    assert stopped(db_factory, occ) == (stopped(db_factory, occ)[0], 1) and stopped(db_factory, occ)[0] is not None   # no retries
    assert counts(db_factory)["Exam"] == 0 and counts(db_factory)["ExamPlan"] == 0     # nothing cut, nothing generated
    monkeypatch.setattr(settings, "global_test_max_questions", 5000)
    assert retry(client, login, gs["sa"], occ).json()["retried"] is True
    [ok] = run(db_factory, t, occ)
    assert not ok["failed"] and counts(db_factory)["Exam"] == 1


def test_the_super_admin_requeues_a_failed_delivery_without_duplicates(client, world, gs, uni, login, db_factory):  # noqa: F811
    from institute_common.models.exams import ExamOutbox
    from inst_exam.services import outbox
    t, occ = uni
    run(db_factory, t, occ)
    [(exam_id, _papers)] = exams_by_student(db_factory, occ["occurrence_id"])["STU000001"]

    class Refusing:
        def send(self, payload):
            raise outbox.PermanentPublishError("receiver rejected the exam (422)")
    with db_factory() as db:
        outbox.deliver_due(db, publisher=Refusing())
        assert [r.status for r in db.query(ExamOutbox)] == ["FAILED"]
    url = f"{API}/exams/{exam_id}/outbox/requeue"
    body = {"reason": "Student service fixed"}
    assert client.post(url, json=body, headers={**login(gs["admin"]), "Idempotency-Key": "requeue-0001"}).status_code == 403
    r = client.post(url, json=body, headers={**login(gs["sa"]), "Idempotency-Key": "requeue-0001"})
    assert r.status_code == 200 and r.json()["requeued"] == ["PUBLISH"], r.text
    assert len(deliver(db_factory)) == 1                                                  # the same row, sent again
    again = client.post(url, json=body, headers={**login(gs["sa"]), "Idempotency-Key": "requeue-0002"}).json()
    assert again["requeued"] == [] and again["deliveries"] == [{"operation": "PUBLISH", "status": "SENT"}]
    c = counts(db_factory)
    assert (c["Exam"], c["StudentExamAssignment"], c["ExamOutbox"]) == (1, 4, 1), c


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
    assert (c["ExamPlan"], c["Exam"], c["StudentExamAssignment"], c["ExamOutbox"]) == (1, 1, 4, 1), c
    keys = [n["dedupe"] + ",".join(n["staff_ids"]) for n in notifications(db_factory)]
    assert len(keys) == len(set(keys))                                                    # no notification twice
