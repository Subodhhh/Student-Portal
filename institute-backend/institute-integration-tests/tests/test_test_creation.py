"""Test creation: audiences (batches, branch, institute, students), drafts, exam-level marking frozen into the exam and applied at
delivery, and Global Tests (platform-published, provider-scheduled, never modified by a provider)."""
from datetime import timedelta
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from institute_common.database import utcnow
from inst_exam.services import outbox
from tests.test_exams import make_paper

API = "/api/v1"


@pytest.fixture()
def tc(client, world, make_staff, login, db_factory):
    """p1 with two branches (b1: batches A and B; b2: batch C), Physics + Chemistry offered, students:
    S1 (b1, A), S2 (b1, B), S3 (b2, C), S4 (b1 AND b2 - two enrollments), S5 (b1, inactive), S6 (p2 only)."""
    from institute_common.models.academics import ProviderSubject, Subject
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    c = {}
    with db_factory() as db:
        phy, chem = Subject(code="PHYSICS", name="Physics"), Subject(code="CHEM", name="Chemistry")
        db.add_all([phy, chem])
        db.flush()
        c["phy"], c["chem"] = phy.subject_id, chem.subject_id
        for s in (phy, chem):
            db.add(ProviderSubject(provider_id=world["p1"], subject_id=s.subject_id))
        db.add(ProviderSubject(provider_id=world["p2"], subject_id=phy.subject_id))
        a, b, cc, d = (Batch(branch_id=world["p1b1"], name="A"), Batch(branch_id=world["p1b1"], name="B"),
                       Batch(branch_id=world["p1b2"], name="C"), Batch(branch_id=world["p2b1"], name="D"))
        db.add_all([a, b, cc, d])
        db.flush()
        c.update(batch_a=a.batch_id, batch_b=b.batch_id, batch_c=cc.batch_id)
        for eid, sid, prov, br, batch, status in (
            ("e1", "STU000001", "p1", "p1b1", a, "ACTIVE"), ("e2", "STU000002", "p1", "p1b1", b, "ACTIVE"),
            ("e3", "STU000003", "p1", "p1b2", cc, "ACTIVE"), ("e4a", "STU000004", "p1", "p1b1", a, "ACTIVE"),
            ("e4b", "STU000004", "p1", "p1b2", cc, "ACTIVE"), ("e5", "STU000005", "p1", "p1b1", a, "INACTIVE"),
            ("e6", "STU000006", "p2", "p2b1", d, "ACTIVE"),
        ):
            db.add(Enrollment(enrollment_id=eid, provider_id=world[prov], branch_id=world[br], student_id=sid, status=status))
            db.flush()
            db.add(EnrollmentBatch(enrollment_id=eid, batch_id=batch.batch_id))
        db.commit()
    _, c["sa"] = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, c["pa"] = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, c["pa2"] = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    c["phy_paper"], c["phy_v1"] = make_paper(db_factory, world["p1"], world["p1b1"], c["phy"], "Physics Set", "PUBLISHED", n=3, marks="4", neg="1")
    c["chem_paper"], c["chem_v1"] = make_paper(db_factory, world["p1"], world["p1b1"], c["chem"], "Chem Set", "PUBLISHED", n=2, marks="3", neg="0")
    return c


def body(tc, **over):
    now = utcnow()
    b = {"name": "Weekly test", "duration_minutes": 90, "start_at": (now + timedelta(days=1)).isoformat(), "end_at": (now + timedelta(days=1, hours=3)).isoformat(),
         "audience_type": "BATCHES", "batch_ids": [str(tc["batch_a"])],
         "slots": [{"subject_id": str(tc["phy"]), "paper_version_id": str(tc["phy_v1"])}, {"subject_id": str(tc["chem"]), "paper_version_id": str(tc["chem_v1"])}]}
    b.update(over)
    return b


def assigned(client, h, exam_id):
    return sorted(a["student_id"] for a in client.get(f"{API}/exams/{exam_id}/assignments?page_size=100", headers=h).json()["items"])


def generate(client, h, plan_id, key="k"):
    r = client.post(f"{API}/exam-plans/{plan_id}/generate", json={}, headers={**h, "Idempotency-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("audience,extra,expected", [
    ("BATCHES", {"batch_ids": ["A"]}, ["STU000001", "STU000004"]),
    ("BRANCH", {}, ["STU000001", "STU000002", "STU000004"]),
    ("INSTITUTE", {}, ["STU000001", "STU000002", "STU000003", "STU000004"]),
    ("STUDENTS", {"student_ids": ["STU000003", "STU000004"]}, ["STU000003", "STU000004"]),
])
def test_each_audience_resolves_current_active_enrollments_once(client, world, tc, login, audience, extra, expected):
    h = login(tc["pa"])
    if "batch_ids" in extra:
        extra = {"batch_ids": [str(tc["batch_a"])]}
    b = body(tc, audience_type=audience, batch_ids=extra.get("batch_ids", []), student_ids=extra.get("student_ids", []))
    preview = client.post(f"{API}/branches/{world['p1b1']}/audience-preview",
                          json={k: b[k] for k in ("audience_type", "batch_ids", "student_ids")}, headers=h)
    assert preview.status_code == 200 and preview.json()["eligible_count"] == len(expected)
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=b, headers=h)
    assert plan.status_code == 201, plan.text
    job = generate(client, h, plan.json()["plan_id"])
    assert assigned(client, h, job["exam_id"]) == expected            # STU000004 (two branches) is assigned once; STU000005 inactive


def test_students_audience_rejects_students_outside_the_provider(client, world, tc, login):
    h = login(tc["pa"])
    r = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, audience_type="STUDENTS", batch_ids=[], student_ids=["STU000001", "STU000006"]), headers=h)
    assert r.status_code == 422 and r.json()["detail"]["student_ids"] == ["STU000006"]
    r = client.post(f"{API}/branches/{world['p1b1']}/audience-preview", json={"audience_type": "STUDENTS", "student_ids": ["STU000006"]}, headers=h)
    assert r.json() == {"eligible_count": 0, "sample_student_ids": [], "not_eligible_student_ids": ["STU000006"],
                        "excluded_no_class": 0, "excluded_other_class": 0}
    assert client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, audience_type="BRANCH", batch_ids=[str(tc["batch_a"])]),
                       headers=h).status_code == 422      # batch ids only for a BATCHES audience
    # another provider's admin cannot target this provider's branch at all
    assert client.post(f"{API}/branches/{world['p1b1']}/audience-preview", json={"audience_type": "BRANCH"}, headers=login(tc["pa2"])).status_code == 403


def test_draft_plans_never_generate_until_activated(client, world, tc, login):
    h = login(tc["pa"])
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, status="DRAFT"), headers=h).json()
    assert plan["status"] == "DRAFT"
    r = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers={**h, "Idempotency-Key": "x"})
    assert r.status_code == 409
    assert client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"status": "ACTIVE"}, headers=h).json()["status"] == "ACTIVE"
    assert generate(client, h, plan["plan_id"])["status"] == "SUCCEEDED"


def test_uniform_marking_is_frozen_into_the_exam_and_delivered(client, world, tc, login, db_factory):
    h = login(tc["pa"])
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, marking_mode="UNIFORM", marks_correct=4, marks_wrong=1), headers=h).json()
    job = generate(client, h, plan["plan_id"])
    exam = client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()
    assert exam["total_marks"] == "20.00" and exam["total_questions"] == 5          # 5 questions x 4, whatever each question said
    assert exam["settings"]["marking"] == {"mode": "UNIFORM", "correct": 4, "wrong": 1}
    assert exam["settings"]["policies"]["unanswered"] == "ZERO" and exam["settings"]["policies"]["attempts"] == "ONE_WITH_RESUME"
    with db_factory() as db:
        payload = outbox.build_payload(db, UUID(exam["exam_version_id"]))
    assert {(q["marks"], q["negative_marks"]) for q in payload["questions"]} == {(4, 1)} and payload["total_marks"] == 20
    # changing the plan afterwards changes future exams only
    assert client.patch(f"{API}/exam-plans/{plan['plan_id']}", json={"marking_mode": "PER_QUESTION"}, headers=h).json()["marking_mode"] == "PER_QUESTION"
    assert client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()["settings"]["marking"]["mode"] == "UNIFORM"
    with db_factory() as db:
        assert {(q["marks"], q["negative_marks"]) for q in outbox.build_payload(db, UUID(exam["exam_version_id"]))["questions"]} == {(4, 1)}


def test_per_question_marking_stays_the_default(client, world, tc, login, db_factory):
    h = login(tc["pa"])
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc), headers=h).json()
    assert plan["marking_mode"] == "PER_QUESTION" and plan["marks_correct"] is None
    exam = client.get(f"{API}/exams/{generate(client, h, plan['plan_id'])['exam_id']}", headers=h).json()
    assert exam["total_marks"] == "18.00"                                           # 3 x 4 + 2 x 3
    with db_factory() as db:
        marks = sorted({(q["marks"], q["negative_marks"]) for q in outbox.build_payload(db, UUID(exam["exam_version_id"]))["questions"]})
    assert marks == [(3, 0), (4, 1)]
    bad = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=body(tc, marking_mode="UNIFORM", marks_correct=4), headers=h)
    assert bad.status_code == 422


def test_the_exam_settings_cannot_be_rewritten(db_factory, client, world, tc, login):
    """exam_versions.settings is part of the immutable manifest (the PostgreSQL trigger also blocks it; see test_postgres_triggers)."""
    from institute_common.migrations import TRIGGERS
    clause = next(c for name, _, c, _ in TRIGGERS["exam"] if name == "exam_versions_immutable")
    assert "settings" in clause


# ------------------------------------------------------------------ Global Tests

@pytest.fixture()
def gtest(client, world, tc, login, db_factory):
    """A published platform paper for Physics, and a draft global test using it."""
    from institute_common.models.papers import Paper
    _, gv = make_paper(db_factory, world["p1"], world["p1b1"], tc["phy"], "Platform Physics", "PUBLISHED", n=4, marks="4", neg="1")
    with db_factory() as db:   # make it a platform paper (scope GLOBAL, no provider/branch)
        from institute_common.models.papers import PaperVersion
        paper = db.get(Paper, db.get(PaperVersion, gv).paper_id)
        paper.scope, paper.provider_id, paper.branch_id = "GLOBAL", None, None
        db.commit()
    h = login(tc["sa"])
    t = client.post(f"{API}/global-tests", json={"name": "Daily Physics #1", "exam_type": "NEET", "target_class": "12th", "duration_minutes": 30,
                                                 "sections": [{"paper_version_id": str(gv)}]}, headers=h)
    assert t.status_code == 201, t.text
    with db_factory() as db:   # the test is for Class 12th: the fixture's enrollments are in 12th (Class matching is exercised positively)
        from institute_common.models.academics import EnrollmentAcademicProfile
        from institute_common.models.org import Enrollment
        for (eid,) in db.query(Enrollment.enrollment_id):
            db.add(EnrollmentAcademicProfile(enrollment_id=eid, class_level="12TH", academic_year="2026-2027"))
        db.commit()
    return {"test_id": t.json()["test_id"], "version": gv}


def test_global_tests_are_platform_managed_and_hidden_until_published(client, world, tc, gtest, login):
    h = login(tc["pa"])
    assert client.post(f"{API}/global-tests", json={"name": "Mine", "duration_minutes": 30, "sections": [{"paper_version_id": str(gtest["version"])}]},
                       headers=h).status_code == 403
    assert client.get(f"{API}/global-tests", headers=h).json()["total"] == 0          # drafts are invisible to providers
    assert client.get(f"{API}/global-tests/{gtest['test_id']}", headers=h).status_code == 404
    h = login(tc["sa"])
    t = client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=h).json()
    assert t["status"] == "PUBLISHED" and t["question_count"] == 4 and t["total_marks"] == "16.00" and t["sections"][0]["subject"] == "Physics"
    assert client.put(f"{API}/global-tests/{gtest['test_id']}", json={"name": "Changed", "duration_minutes": 45,
                                                                      "sections": [{"paper_version_id": str(gtest["version"])}]}, headers=h).status_code == 409
    h = login(tc["pa"])
    listed = client.get(f"{API}/global-tests", headers=h).json()["items"]
    assert [x["name"] for x in listed] == ["Daily Physics #1"] and listed[0]["duration_minutes"] == 30


def test_a_section_must_be_an_approved_platform_paper(client, world, tc, login):
    h = login(tc["sa"])
    r = client.post(f"{API}/global-tests", json={"name": "Bad", "duration_minutes": 30, "sections": [{"paper_version_id": str(tc["phy_v1"])}]}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "PAPER_NOT_AVAILABLE"     # a provider's paper is not platform content


def test_scheduling_a_global_test_locks_its_papers_duration_and_marking(client, world, tc, gtest, login, db_factory):
    h = login(tc["sa"])
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=h)
    h = login(tc["pa"])
    now = utcnow()
    sched = {"branch_id": str(world["p1b1"]), "audience_type": "INSTITUTE", "start_at": (now + timedelta(hours=2)).isoformat(),
             "end_at": (now + timedelta(hours=5)).isoformat()}
    plan = client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", json=sched, headers=h)
    assert plan.status_code == 201, plan.text
    plan = plan.json()
    assert plan["global_test_name"] == "Daily Physics #1" and plan["duration_minutes"] == 30 and plan["marking_mode"] == "PER_QUESTION"
    assert plan["slots"] == [{"subject_id": str(tc["phy"]), "paper_id": None, "paper_version_id": str(gtest["version"])}]
    pid = plan["plan_id"]
    assert client.put(f"{API}/exam-plans/{pid}/slots", json=[{"subject_id": str(tc["phy"]), "paper_version_id": str(tc["phy_v1"])}], headers=h).status_code == 409
    for change in ({"duration_minutes": 60}, {"marking_mode": "UNIFORM", "marks_correct": 1, "marks_wrong": 0}):
        assert client.patch(f"{API}/exam-plans/{pid}", json=change, headers=h).status_code == 409
    assert client.patch(f"{API}/exam-plans/{pid}", json={"name": "Daily physics (morning)"}, headers=h).status_code == 200
    assert client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", json=sched, headers=h).status_code == 409      # same branch & time
    job = generate(client, h, pid)
    exam = client.get(f"{API}/exams/{job['exam_id']}", headers=h).json()
    assert exam["papers"][0]["paper_version_id"] == str(gtest["version"]) and exam["papers"][0]["platform_paper"] is True
    assert exam["settings"]["global_test_id"] == gtest["test_id"]
    assert assigned(client, h, job["exam_id"]) == ["STU000001", "STU000002", "STU000003", "STU000004"]
    # the published test is unchanged by the provider's scheduling
    t = client.get(f"{API}/global-tests/{gtest['test_id']}", headers=h).json()
    assert t["duration_minutes"] == 30 and t["sections"][0]["paper_version_id"] == str(gtest["version"])


def test_the_same_global_test_is_never_assigned_twice_for_the_same_time(client, world, tc, gtest, login):
    h = login(tc["sa"])
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=h)
    h = login(tc["pa"])
    start, end = utcnow() + timedelta(hours=2), utcnow() + timedelta(hours=5)
    first = client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", headers=h, json={
        "branch_id": str(world["p1b1"]), "audience_type": "BRANCH", "start_at": start.isoformat(), "end_at": end.isoformat()}).json()
    second = client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", headers=h, json={
        "branch_id": str(world["p1b2"]), "audience_type": "BRANCH", "start_at": start.isoformat(), "end_at": end.isoformat()}).json()
    e1 = generate(client, h, first["plan_id"])["exam_id"]
    e2 = generate(client, h, second["plan_id"], key="k2")["exam_id"]
    assert assigned(client, h, e1) == ["STU000001", "STU000002", "STU000004"]
    assert assigned(client, h, e2) == ["STU000003"]                 # STU000004 (both branches) already holds it for that time
    assert client.get(f"{API}/exams/{e2}", headers=h).json()["settings"]["skipped_duplicates"] == 1


def test_withdrawing_stops_new_schedules_and_keeps_generated_exams(client, world, tc, gtest, login):
    h = login(tc["sa"])
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=h)
    h = login(tc["pa"])
    start, end = utcnow() + timedelta(hours=2), utcnow() + timedelta(hours=5)
    plan = client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", headers=h, json={
        "branch_id": str(world["p1b1"]), "audience_type": "BRANCH", "start_at": start.isoformat(), "end_at": end.isoformat()}).json()
    exam_id = generate(client, h, plan["plan_id"])["exam_id"]
    h = login(tc["sa"])
    assert client.post(f"{API}/global-tests/{gtest['test_id']}/withdraw", json={"reason": "x"}, headers=h).status_code == 422   # reason required
    assert client.post(f"{API}/global-tests/{gtest['test_id']}/withdraw", json={"reason": "Wrong answer in Q3"}, headers=h).json()["status"] == "WITHDRAWN"
    h = login(tc["pa"])
    assert client.get(f"{API}/exam-plans/{plan['plan_id']}", headers=h).json()["status"] == "INACTIVE"
    assert client.get(f"{API}/exams/{exam_id}", headers=h).json()["status"] == "PUBLISHED"      # already generated: unchanged
    assert client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", headers=h, json={
        "branch_id": str(world["p1b1"]), "audience_type": "BRANCH", "start_at": (start + timedelta(days=1)).isoformat(),
        "end_at": (end + timedelta(days=1)).isoformat()}).status_code == 404               # no longer offered to providers
    h = login(tc["sa"])
    copy = client.post(f"{API}/global-tests/{gtest['test_id']}/derive", headers=h).json()
    assert copy["status"] == "DRAFT" and copy["derived_from_test_id"] == gtest["test_id"] and copy["name"] == "Daily Physics #1 (copy)"
    assert client.get(f"{API}/global-tests/{gtest['test_id']}", headers=h).json()["status"] == "WITHDRAWN"


def test_scheduling_needs_the_subjects_offered(client, world, tc, gtest, login, db_factory):
    from institute_common.models.academics import ProviderSubject
    h = login(tc["sa"])
    client.post(f"{API}/global-tests/{gtest['test_id']}/publish", headers=h)
    with db_factory() as db:
        db.query(ProviderSubject).filter_by(provider_id=world["p2"]).delete()
        db.commit()
    start = utcnow() + timedelta(hours=2)
    r = client.post(f"{API}/global-tests/{gtest['test_id']}/schedule", headers=login(tc["pa2"]), json={
        "branch_id": str(world["p2b1"]), "audience_type": "BRANCH", "start_at": start.isoformat(), "end_at": (start + timedelta(hours=2)).isoformat()})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "SUBJECT_NOT_ENABLED"
