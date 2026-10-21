"""Global Tests, end to end through the API: the platform creates, configures and publishes a test; providers schedule it for their own
students (the exam plan is locked to the test's exact papers); generation, the Scheduled Tests view of one Global Test, new versions
and withdrawal. Platform-only actions stay platform-only; published versions, generated exams and their students never change."""
from datetime import timedelta
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from institute_common.database import utcnow
from tests.test_global_contributions import client, login  # noqa: F401  (one session per staff member: the MFA code limit)
from tests.test_question_bank import bank, create, q_body  # noqa: F401  (fixtures)

API = "/api/v1"


@pytest.fixture()
def gw(client, world, bank, make_staff, login, db_factory):  # noqa: F811
    """bank + a draft Global Test (Physics platform paper), batch A in p1-b1 with two students, a Branch Admin of p1-b1."""
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    c = dict(bank)
    with db_factory() as db:
        a = Batch(branch_id=world["p1b1"], name="A")
        db.add(a)
        db.flush()
        c["batch_a"] = a.batch_id
        for eid, sid in (("g1", "STU000001"), ("g2", "STU000002")):
            db.add(Enrollment(enrollment_id=eid, provider_id=world["p1"], branch_id=world["p1b1"], student_id=sid))
            db.flush()
            db.add(EnrollmentBatch(enrollment_id=eid, batch_id=a.batch_id))
        db.commit()
    _, c["ba"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(c["sa"])
    qids = [create(client, login, c["sa"], q_body(c["phy"], scope="GLOBAL", stem=f"Global physics {i}?")) for i in range(2)]
    h = login(c["sa"])
    pv = client.post(f"{API}/papers", json={"scope": "GLOBAL", "subject_id": str(c["phy"]), "title": "Global Physics", "question_ids": qids}, headers=h).json()["paper_version_id"]
    client.post(f"{API}/paper-versions/{pv}/approve", json={}, headers=h)
    client.post(f"{API}/paper-versions/{pv}/publish", json={}, headers=h)
    r = client.post(f"{API}/global-tests", json={"name": "National Mock", "duration_minutes": 45, "sections": [{"paper_version_id": pv}]}, headers=h)
    assert r.status_code == 201, r.text
    c["test_id"], c["pv"] = r.json()["test_id"], pv
    return c


def schedule_body(world, gw, branch="p1b1", minutes=60, **over):
    start = utcnow() + timedelta(minutes=minutes)
    return {"branch_id": str(world[branch]), "audience_type": "BATCHES", "batch_ids": [str(gw["batch_a"])], "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=2)).isoformat(), **over}


def test_platform_only_actions_stay_platform_only(client, world, gw, login):
    tid = gw["test_id"]
    body = {"name": "Hijacked", "duration_minutes": 10, "sections": [{"paper_version_id": gw["pv"]}]}
    for who in ("pa1", "ba", "teacher"):
        h = login(gw[who])
        assert client.post(f"{API}/global-tests", json=body, headers=h).status_code == 403, who
        assert client.get(f"{API}/global-tests/{tid}", headers=h).status_code == 404, who            # drafts are not revealed
    h = login(gw["sa"])
    assert client.post(f"{API}/global-tests/{tid}/publish", headers=h).json()["status"] == "PUBLISHED"
    for who in ("pa1", "ba", "teacher"):
        h = login(gw[who])
        assert client.get(f"{API}/global-tests/{tid}", headers=h).status_code == 200, who
        for action, payload in (("publish", None), ("withdraw", {"reason": "not mine"}), ("derive", None)):
            assert client.post(f"{API}/global-tests/{tid}/{action}", json=payload, headers=h).status_code == 403, (who, action)
        assert client.put(f"{API}/global-tests/{tid}", json=body, headers=h).status_code == 403, who


def test_only_examplan_managers_schedule_and_only_for_their_own_branches(client, world, gw, login):
    tid = gw["test_id"]
    h = login(gw["sa"])
    assert client.post(f"{API}/global-tests/{tid}/schedule", json=schedule_body(world, gw), headers=h).status_code == 409   # draft
    client.post(f"{API}/global-tests/{tid}/publish", headers=h)
    for who in ("ba", "teacher"):
        assert client.post(f"{API}/global-tests/{tid}/schedule", json=schedule_body(world, gw), headers=login(gw[who])).status_code == 403, who
    assert client.post(f"{API}/global-tests/{tid}/schedule", json=schedule_body(world, gw), headers=login(gw["pa2"])).status_code == 403
    assert client.post(f"{API}/global-tests/{tid}/schedule", json=schedule_body(world, gw), headers=login(gw["pa1"])).status_code == 201
    assert client.post(f"{API}/global-tests/{tid}/schedule", json=schedule_body(world, gw, minutes=300), headers=login(gw["sa"])).status_code == 201


def test_the_complete_scheduling_workflow(client, world, gw, login, db_factory):
    from institute_common.models.exams import Exam, ExamPlanSubject, ExamVersion, ExamVersionPaper, GlobalTestSection, StudentExamAssignment
    tid = gw["test_id"]
    client.post(f"{API}/global-tests/{tid}/publish", headers=login(gw["sa"]))
    h = login(gw["pa1"])
    r = client.post(f"{API}/global-tests/{tid}/schedule", json=schedule_body(world, gw), headers=h)
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["global_test_id"] == tid and plan["duration_minutes"] == 45 and plan["marking_mode"] == "PER_QUESTION"
    again = client.post(f"{API}/global-tests/{tid}/schedule", json={**schedule_body(world, gw), "start_at": plan["start_at"], "end_at": plan["end_at"]}, headers=h)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_SCHEDULED"          # no duplicate schedule
    with db_factory() as db:
        assert [s.paper_version_id for s in db.query(ExamPlanSubject).filter_by(plan_id=UUID(plan["plan_id"]))] == \
               [s.paper_version_id for s in db.query(GlobalTestSection).filter_by(test_id=UUID(tid))]        # locked to the test's papers
    listed = client.get(f"{API}/tests?global_test_id={tid}", headers=h).json()["items"]
    assert [(x["plan_id"], x["test_type"]) for x in listed] == [(plan["plan_id"], "GLOBAL")]
    assert client.get(f"{API}/tests?global_test_id={tid}", headers=login(gw["pa2"])).json()["items"] == []     # other providers
    h = login(gw["pa1"])
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=h).json()
    assert job["status"] == "SUCCEEDED", job
    with db_factory() as db:
        exam = db.query(Exam).filter_by(plan_id=UUID(plan["plan_id"])).one()
        ev = db.query(ExamVersion).filter_by(exam_id=exam.exam_id).one()
        assert [str(p.paper_version_id) for p in db.query(ExamVersionPaper).filter_by(exam_version_id=ev.exam_version_id)] == [gw["pv"]]
        assert sorted(a.student_id for a in db.query(StudentExamAssignment).filter_by(exam_id=exam.exam_id)) == ["STU000001", "STU000002"]
        assert ev.settings["global_test_id"] == tid and exam.status == "PUBLISHED"
    # a new version leaves this one, its schedule and its exam alone
    h = login(gw["sa"])
    v2 = client.post(f"{API}/global-tests/{tid}/derive", headers=h).json()
    assert v2["status"] == "DRAFT" and v2["derived_from_test_id"] == tid
    assert client.put(f"{API}/global-tests/{tid}", json={"name": "Changed", "duration_minutes": 10, "sections": [{"paper_version_id": gw["pv"]}]},
                      headers=h).status_code == 409                                                        # published: immutable
    w = client.post(f"{API}/global-tests/{tid}/withdraw", json={"reason": "Replaced by version 2"}, headers=h)
    assert w.status_code == 200 and w.json()["status"] == "WITHDRAWN"
    assert client.get(f"{API}/exam-plans/{plan['plan_id']}", headers=h).json()["status"] == "INACTIVE"
    assert client.post(f"{API}/global-tests/{tid}/schedule", json=schedule_body(world, gw, minutes=500), headers=login(gw["pa1"])).status_code == 404
    with db_factory() as db:
        assert db.query(Exam).filter_by(plan_id=UUID(plan["plan_id"])).one().status == "PUBLISHED"                 # generated exam unchanged
        assert db.query(StudentExamAssignment).filter_by(exam_id=exam.exam_id).count() == 2
