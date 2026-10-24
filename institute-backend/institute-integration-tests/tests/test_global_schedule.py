"""Global Tests with a central schedule: the platform plans and publishes dates (no platform paper needed); provider staff submit their
own papers per date, subject and audience; at the deadline every student receives exactly their own teacher's paper.

World: provider p1 (two branches: b1 with batches A and B, b2 with batch C), teachers tA (Physics, batch A), tB (Physics, batch B), tC
(Chemistry, all of b1), a Branch Admin of b1 and the Provider Admin; provider p2 is a single tutor (Provider Admin, one branch, no
batches) whose students are listed individually. STU000001 studies with both providers (one identity)."""
from datetime import timedelta
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from institute_common.database import utcnow
from inst_exam.services import global_generation as gg
from tests.test_exams import make_paper
from tests.test_global_contributions import client, login  # noqa: F401  (one session per staff member)

API = "/api/v1"


def next_months(n=2):
    today = utcnow().date()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        m += 1
        if m > 12:
            y, m = y + 1, 1
        out.append(f"{y:04d}-{m:02d}")
    return out


@pytest.fixture()
def gs(world, make_staff, db_factory):
    from institute_common.models.academics import ProviderSubject, Subject, TeacherSubjectAssignment
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    from institute_common.models.papers import Paper, PaperVersion
    c = {}
    with db_factory() as db:
        subs = {k: Subject(code=k.upper(), name=n) for k, n in (("phy", "Physics"), ("chem", "Chemistry"), ("bio", "Biology"))}
        db.add_all(subs.values())
        db.flush()
        c.update({k: s.subject_id for k, s in subs.items()})
        for prov, keys in (("p1", ("phy", "chem")), ("p2", ("phy",))):
            for k in keys:
                db.add(ProviderSubject(provider_id=world[prov], subject_id=c[k]))
        batches = {k: Batch(branch_id=world[br], name=k.upper()) for k, br in (("a", "p1b1"), ("b", "p1b1"), ("c", "p1b2"))}
        db.add_all(batches.values())
        db.flush()
        c.update({f"batch_{k}": b.batch_id for k, b in batches.items()})
        for eid, sid, prov, br, batch in (("g1", "STU000001", "p1", "p1b1", "a"), ("g2", "STU000002", "p1", "p1b1", "b"),
                                          ("g3", "STU000003", "p1", "p1b2", "c"), ("g4", "STU000004", "p2", "p2b1", None),
                                          ("g5", "STU000001", "p2", "p2b1", None)):
            db.add(Enrollment(enrollment_id=eid, provider_id=world[prov], branch_id=world[br], student_id=sid))
            db.flush()
            if batch:
                db.add(EnrollmentBatch(enrollment_id=eid, batch_id=c[f"batch_{batch}"]))
        db.commit()
    _, c["sa"] = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, c["admin"] = make_staff([(Role.ADMIN, None, None)])
    c["pa_id"], c["pa"] = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, c["ba"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    c["tutor_id"], c["tutor"] = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    for key, subj, batch in (("tA", "phy", "batch_a"), ("tB", "phy", "batch_b"), ("tC", "chem", None), ("tBio", "bio", None)):
        c[f"{key}_id"], c[key] = make_staff([(Role.TEACHER, world["p1"], None)])
        with db_factory() as db:
            db.add(TeacherSubjectAssignment(staff_id=c[f"{key}_id"], provider_id=world["p1"], branch_id=world["p1b1"], subject_id=c[subj],
                                            batch_id=c[batch] if batch else None))
            db.commit()

    def paper(key, owner, provider, branch, subject, status="PUBLISHED", n=3):
        pid, pv = make_paper(db_factory, world[provider], world[branch], c[subject], f"{key} paper", status, n=n)
        with db_factory() as db:
            db.get(Paper, pid).created_by = c[f"{owner}_id"]
            db.commit()
        c[key] = str(pv)
        c[f"{key}_paper"] = pid
    paper("paperA", "tA", "p1", "p1b1", "phy")
    paper("paperB", "tB", "p1", "p1b1", "phy", n=4)
    paper("paperC", "tC", "p1", "p1b1", "chem", n=2)
    paper("paperT", "tutor", "p2", "p2b1", "phy", n=5)
    paper("paperDraft", "tA", "p1", "p1b1", "phy", status="VALIDATED")
    del PaperVersion
    return c


def create_test(client, login, gs, **over):
    body = {"name": "National Science Mock", "duration_minutes": 60, "subject_ids": [str(gs["phy"]), str(gs["chem"])],
            "marking_mode": "UNIFORM", "marks_correct": 4, "marks_wrong": 1, "generate_lead_minutes": 120, **over}
    r = client.post(f"{API}/global-tests", json=body, headers=login(gs["sa"]))
    assert r.status_code == 201, r.text
    return r.json()


def published(client, login, gs, months=1, **over):
    t = create_test(client, login, gs, **over)
    r = client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", headers=login(gs["sa"]),
                    json={"months": next_months(months), "weekdays": [5], "start_time": "10:00", "timezone": "Asia/Kolkata", "window_minutes": 180})
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/global-tests/{t['test_id']}/publish", headers=login(gs["sa"])).json()["status"] == "PUBLISHED"
    occ = client.get(f"{API}/global-tests/{t['test_id']}/occurrences", headers=login(gs["sa"])).json()["items"][0]
    return t, occ


def aud(world, gs, provider="p1", branches=("p1b1",), whole=(), batches=(), students=()):
    return {"provider_id": str(world[provider]), "branch_ids": [str(world[b]) for b in branches], "whole_branch_ids": [str(world[b]) for b in whole],
            "batch_ids": [str(gs[b]) for b in batches], "student_ids": list(students)}


def submit(client, login, who, occ, subject, paper, audience, key=None):
    return client.post(f"{API}/global-occurrences/{occ['occurrence_id']}/submissions", headers={**login(who), "Idempotency-Key": key or f"sub-{who}-{paper}"},
                       json={"subject_id": str(subject), "paper_version_id": paper, "audience": audience})


def exams_of(db_factory, occurrence_id):
    """{student: [(provider, [paper_version_id...])]} for the exams generated for one occurrence."""
    from institute_common.models.exams import Exam, ExamPlan, ExamVersion, ExamVersionPaper, StudentExamAssignment
    out: dict = {}
    with db_factory() as db:
        for plan in db.query(ExamPlan).filter_by(global_occurrence_id=UUID(str(occurrence_id))):
            for exam in db.query(Exam).filter_by(plan_id=plan.plan_id):
                ev = db.query(ExamVersion).filter_by(exam_id=exam.exam_id).one()
                papers = [str(x.paper_version_id) for x in db.query(ExamVersionPaper).filter_by(exam_version_id=ev.exam_version_id).order_by(ExamVersionPaper.position)]
                for a in db.query(StudentExamAssignment).filter_by(exam_id=exam.exam_id):
                    out.setdefault(a.student_id, []).append((plan.provider_id, papers))
    return out


def run_generation(db_factory, test, occ):
    from institute_common.models.exams import GlobalTest, GlobalTestOccurrence
    with db_factory() as db:
        o = db.get(GlobalTestOccurrence, UUID(occ["occurrence_id"]))
        t = db.get(GlobalTest, UUID(test["test_id"]))
        return gg.run_due(db, now=gg.deadline(t, o) + timedelta(minutes=1))


# ------------------------------------------------------------------ Stage A: the platform schedules centrally

def test_platform_plans_months_of_dates_without_any_paper_and_publishes_once(client, world, gs, login):
    t = create_test(client, login, gs)
    assert t["kind"] == "SCHEDULE" and [s["subject"] for s in t["subjects"]] == ["Physics", "Chemistry"] and t["sections"] == []
    h = login(gs["sa"])
    plan = {"months": next_months(2), "weekdays": [5], "start_time": "10:00", "timezone": "Asia/Kolkata", "window_minutes": 180}
    r = client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", json=plan, headers=h).json()
    assert r["created"] >= 8 and r["existing"] == 0
    again = client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", json=plan, headers=h).json()
    assert again["created"] == 0 and again["existing"] == r["created"]                       # idempotent
    items = client.get(f"{API}/global-tests/{t['test_id']}/occurrences", headers=h).json()["items"]
    first, second = items[0], items[1]
    start = (utcnow() + timedelta(days=50)).replace(microsecond=0)
    edited = client.put(f"{API}/global-occurrences/{first['occurrence_id']}", headers=h,
                        json={"start_at": start.isoformat(), "end_at": (start + timedelta(hours=2)).isoformat()})
    assert edited.status_code == 200
    assert client.delete(f"{API}/global-occurrences/{second['occurrence_id']}", headers=h).status_code == 204
    assert client.get(f"{API}/global-schedule", headers=login(gs["pa"])).json()["items"] == []     # drafts are not visible
    pub = client.post(f"{API}/global-tests/{t['test_id']}/publish", headers=login(gs["sa"])).json()
    assert pub["status"] == "PUBLISHED" and pub["occurrences"]["PUBLISHED"] == r["created"] - 1
    h = login(gs["sa"])
    assert client.put(f"{API}/global-occurrences/{first['occurrence_id']}", headers=h,
                      json={"start_at": start.isoformat(), "end_at": (start + timedelta(hours=3)).isoformat()}).status_code == 409   # published: locked
    assert client.delete(f"{API}/global-occurrences/{first['occurrence_id']}", headers=h).status_code == 409
    more = client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", headers=h, json={**plan, "months": next_months(3)[2:]}).json()
    assert more["created"] >= 4
    assert client.post(f"{API}/global-tests/{t['test_id']}/occurrences/publish", json={}, headers=h).json()["published"] == more["created"]


def test_admin_follows_its_permissions_and_providers_cannot_schedule(client, world, gs, login):
    t = create_test(client, login, gs)
    plan = {"months": next_months(1), "weekdays": [2], "start_time": "09:00", "timezone": "UTC", "window_minutes": 60}
    assert client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", json=plan, headers=login(gs["admin"])).status_code == 200
    for who in ("pa", "ba", "tA", "tutor"):
        h = login(gs[who])
        assert client.post(f"{API}/global-tests", json={"name": "Mine", "duration_minutes": 30, "subject_ids": [str(gs["phy"])]}, headers=h).status_code == 403
        assert client.post(f"{API}/global-tests/{t['test_id']}/occurrences/plan", json=plan, headers=h).status_code == 403
        assert client.post(f"{API}/global-tests/{t['test_id']}/publish", headers=h).status_code == 403


# ------------------------------------------------------------------ Stage B: providers see the published schedule

def test_the_published_schedule_reaches_the_staff_it_concerns(client, world, gs, login):
    t, occ = published(client, login, gs)
    for who in ("pa", "ba", "tA", "tC", "tutor"):
        items = client.get(f"{API}/global-schedule", headers=login(gs[who])).json()["items"]
        assert items and items[0]["test_name"] == "National Science Mock", who
    assert client.get(f"{API}/global-schedule", headers=login(gs["tBio"])).json()["items"] == []     # teaches no subject of the test
    mine = {s["subject"]: s["can_submit"] for s in client.get(f"{API}/global-schedule", headers=login(gs["tA"])).json()["items"][0]["subjects"]}
    assert mine == {"Physics": True, "Chemistry": False}
    tutor = {s["subject"]: s["can_submit"] for s in client.get(f"{API}/global-schedule", headers=login(gs["tutor"])).json()["items"][0]["subjects"]}
    assert tutor == {"Physics": True, "Chemistry": False}                                                 # p2 offers only Physics


# ------------------------------------------------------------------ Stage C: authorized staff submit their papers

def test_teachers_submit_their_own_papers_for_their_own_batches(client, world, gs, login):
    t, occ = published(client, login, gs)
    r = submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    assert r.status_code == 201, r.text
    assert r.json()["students_now"] == 1 and r.json()["audience"]["batches"] == ["A"]
    again = submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    assert again.json()["submission_id"] == r.json()["submission_id"]                                    # retry: same submission
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_b",)), key="x-other-batch").status_code == 422
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_a",)), key="x-foreign").status_code == 404
    r = submit(client, login, gs["tA"], occ, gs["chem"], gs["paperC"], aud(world, gs, batches=("batch_a",)), key="x-chem-paper")
    assert r.status_code == 404, r.text
    r = submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",)))
    assert r.status_code == 201, r.text                                                                   # a second Physics paper, other students
    assert submit(client, login, gs["sa"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)), key="x-platform").status_code == 403


def test_an_overlapping_audience_is_refused_instead_of_overriding_a_teacher(client, world, gs, login):
    t, occ = published(client, login, gs)
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",))).status_code == 201
    r = submit(client, login, gs["ba"], occ, gs["phy"], gs["paperB"], aud(world, gs, whole=("p1b1",)), key="ba-whole-branch")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AUDIENCE_OVERLAP" and r.json()["detail"]["overlapping_students"] == 1
    assert submit(client, login, gs["ba"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",)), key="ba-batch-b").status_code == 201


def test_a_tutor_without_batches_submits_for_students_listed_individually(client, world, gs, login):
    t, occ = published(client, login, gs)
    r = submit(client, login, gs["tutor"], occ, gs["phy"], gs["paperT"], aud(world, gs, provider="p2", branches=("p2b1",), students=("STU000004", "STU000001")))
    assert r.status_code == 201 and r.json()["students_now"] == 2, r.text
    assert submit(client, login, gs["tutor"], occ, gs["phy"], gs["paperA"], aud(world, gs, provider="p2", branches=("p2b1",), students=("STU000004",)),
                  key="tutor-foreign").status_code == 404                                                # another provider's paper
    assert submit(client, login, gs["tutor"], occ, gs["phy"], gs["paperT"], aud(world, gs, provider="p2", branches=("p2b1",), students=("STU000002",)),
                  key="tutor-not-mine").status_code == 422                                               # not the tutor's student


def test_submissions_are_visible_only_within_scope(client, world, gs, login):
    t, occ = published(client, login, gs)
    submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",)))
    submit(client, login, gs["tutor"], occ, gs["phy"], gs["paperT"], aud(world, gs, provider="p2", branches=("p2b1",), students=("STU000004",)))

    def titles(who):
        return sorted(x["paper_title"] for x in client.get(f"{API}/global-occurrences/{occ['occurrence_id']}/submissions", headers=login(gs[who])).json()["items"])
    assert titles("tA") == ["paperA paper"] and titles("tB") == ["paperB paper"]                       # teachers: their own only
    assert titles("ba") == titles("pa") == ["paperA paper", "paperB paper"]
    assert titles("tutor") == ["paperT paper"]
    assert titles("sa") == ["paperA paper", "paperB paper", "paperT paper"]
    cov = {(c["provider_name"], c["subject"]): c for c in client.get(f"{API}/global-occurrences/{occ['occurrence_id']}", headers=login(gs["sa"])).json()["coverage"]}
    assert cov[("Provider p1", "Physics")]["students_covered"] == 2 and cov[("Provider p1", "Chemistry")]["missing"] is True


# ------------------------------------------------------------------ Stage D/E: isolation, generation, stability

def test_each_student_receives_only_their_own_teachers_paper(client, world, gs, login, db_factory):
    from institute_common.models.exams import ExamNotificationOutbox, ExamOutbox, ExamPlan
    test, occ = published(client, login, gs)
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",))).status_code == 201
    assert submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",))).status_code == 201
    assert submit(client, login, gs["tC"], occ, gs["chem"], gs["paperC"], aud(world, gs, whole=("p1b1",))).status_code == 201
    assert submit(client, login, gs["tutor"], occ, gs["phy"], gs["paperT"], aud(world, gs, provider="p2", branches=("p2b1",),
                  students=("STU000004", "STU000001"))).status_code == 201
    [run] = run_generation(db_factory, test, occ)
    assert not run["failed"] and run["students"] == 4, run
    got = exams_of(db_factory, occ["occurrence_id"])
    p1, p2 = world["p1"], world["p2"]
    assert sorted(got["STU000001"]) == sorted([(p1, [gs["paperA"], gs["paperC"]]), (p2, [gs["paperT"]])])   # both providers, no leakage
    assert got["STU000002"] == [(p1, [gs["paperB"], gs["paperC"]])]
    assert got["STU000004"] == [(p2, [gs["paperT"]])]
    assert "STU000003" not in got                                                    # no submission covers batch C: never another paper
    with db_factory() as db:
        plans = db.query(ExamPlan).filter_by(global_occurrence_id=UUID(occ["occurrence_id"])).count()
        assert plans == 3 and db.query(ExamOutbox).count() == 3                       # one exam per paper set, each delivered
        assert any(o.payload["kind"] == "GLOBAL_TEST_MISSING_PAPERS" for o in db.query(ExamNotificationOutbox))   # batch C students in p1? reported per provider
    assert run_generation(db_factory, test, occ) == []                               # generated once: a retry does nothing
    assert exams_of(db_factory, occ["occurrence_id"]) == got


def test_after_the_deadline_exams_are_fixed_even_if_teaching_changes(client, world, gs, login, db_factory):
    from institute_common.models.academics import TeacherSubjectAssignment
    test, occ = published(client, login, gs)
    submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    run_generation(db_factory, test, occ)
    before = exams_of(db_factory, occ["occurrence_id"])
    assert submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",))).status_code == 409   # closed
    with db_factory() as db:
        db.query(TeacherSubjectAssignment).filter_by(staff_id=gs["tA_id"]).update({"status": "INACTIVE"})
        db.commit()
    assert exams_of(db_factory, occ["occurrence_id"]) == before and before["STU000001"] == [(world["p1"], [gs["paperA"]])]


def test_ambiguous_students_get_neither_paper_and_unapproved_papers_are_not_used(client, world, gs, login, db_factory):
    from institute_common.models.exams import ExamNotificationOutbox
    from institute_common.models.org import EnrollmentBatch
    test, occ = published(client, login, gs)
    submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    submit(client, login, gs["tB"], occ, gs["phy"], gs["paperB"], aud(world, gs, batches=("batch_b",)))
    with db_factory() as db:                       # after both submissions STU000002 also joins batch A: now covered twice for Physics
        db.add(EnrollmentBatch(enrollment_id="g2", batch_id=gs["batch_a"]))
        db.commit()
    [run] = run_generation(db_factory, test, occ)
    got = exams_of(db_factory, occ["occurrence_id"])
    assert run["ambiguous"] == 1 and "STU000002" not in got and got["STU000001"] == [(world["p1"], [gs["paperA"]])]
    with db_factory() as db:
        bodies = [o.payload["body"] for o in db.query(ExamNotificationOutbox) if o.payload["kind"] == "GLOBAL_TEST_MISSING_PAPERS"]
        assert any("received neither paper" in b for b in bodies)

    test2, occ2 = published(client, login, gs)
    submit(client, login, gs["tA"], occ2, gs["phy"], gs["paperDraft"], aud(world, gs, batches=("batch_a",)), key="draft-paper")
    [run2] = run_generation(db_factory, test2, occ2)
    assert run2["students"] == 0 and exams_of(db_factory, occ2["occurrence_id"]) == {}
    assert any("not approved by the deadline" in p for m in run2["missing"] for p in m["problems"])


def test_cancelled_and_withdrawn_dates_take_no_papers(client, world, gs, login, db_factory):
    test, occ = published(client, login, gs)
    r = submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)))
    sid = r.json()["submission_id"]
    assert client.post(f"{API}/global-paper-submissions/{sid}/withdraw", json={"reason": "x"}, headers=login(gs["tB"])).status_code == 404
    w = client.post(f"{API}/global-paper-submissions/{sid}/withdraw", json={"reason": "Wrong paper chosen"}, headers=login(gs["tA"]))
    assert w.status_code == 200 and w.json()["status"] == "WITHDRAWN"
    assert client.post(f"{API}/global-occurrences/{occ['occurrence_id']}/cancel", json={"reason": "Exam board holiday"}, headers=login(gs["sa"])).status_code == 200
    assert submit(client, login, gs["tA"], occ, gs["phy"], gs["paperA"], aud(world, gs, batches=("batch_a",)), key="after-cancel").status_code in (404, 409)
    assert run_generation(db_factory, test, occ) == []
