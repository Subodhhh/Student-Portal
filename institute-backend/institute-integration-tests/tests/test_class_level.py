"""Class / Academic Level and Academic Year per provider enrollment, batch defaults, and Class-aware exam audiences.

Fixtures (tc/ct/tv): p1-b1 batch A = STU000001 (e1), STU000004 (e4a); batch B = STU000002 (e2); p1-b2 batch C = STU000003 (e3),
STU000004 (e4b); STU000001 is also enrolled with provider p2 (e1p2, batch D). Teachers: phy_a / chem_a (batch A). The test-creation
helpers publish and generate at once."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from tests.test_create_test import aud, ct, draft, publish  # noqa: F401  (fixtures and helpers)
from tests.test_teacher_visibility import students_of, tv  # noqa: F401
from tests.test_test_creation import tc  # noqa: F401  (fixture)

API = "/api/v1"
Y = "2026-2027"


def prof(db_factory, eid, code, year=Y, other=None):
    from institute_common.models.academics import EnrollmentAcademicProfile
    with db_factory() as db:
        row = db.get(EnrollmentAcademicProfile, eid)
        if row is None:
            db.add(EnrollmentAcademicProfile(enrollment_id=eid, class_level=code, academic_year=year, other_class_description=other))
        else:
            row.class_level, row.academic_year, row.other_class_description = code, year, other
        db.commit()


def roster(client, h, branch):
    return {e["enrollment_id"]: e for e in client.get(f"{API}/branches/{branch}/enrollments", headers=h).json()["items"]}


def physics(client, h, world, c, name, cls=None, **audience):
    body = draft(world, c, name=name, audience=aud(world, c, **audience), subjects=[("phy", str(c["phy_v1"]))])
    if cls is not None:
        body["target_class"] = cls
    return publish(client, h, body)


# ------------------------------------------------------------------------------------------------ enrollment profile API

def test_enrolling_requires_a_valid_class_and_academic_year(client, world, ct, login):
    h = login(ct["pa"])
    url = f"{API}/branches/{world['p1b1']}/enrollments"
    base = {"student_id": "STU000005", "batch_ids": [str(ct["batch_a"])]}
    for bad in (None, {"class_level": "11th"}, {"class_level": "13th", "academic_year": Y}, {"class_level": "Class 11", "academic_year": Y},
                {"class_level": "11th", "academic_year": "2026-2028"}, {"class_level": "11th", "academic_year": "2026/27"},
                {"class_level": "Other", "academic_year": Y}, {"class_level": "11th", "academic_year": Y, "other_class_description": "x"}):
        body = dict(base, **({"academic": bad} if bad is not None else {}))
        assert client.post(url, json=body, headers=h).status_code == 422, bad
    r = client.post(url, json=dict(base, academic={"class_level": " other ", "academic_year": Y, "other_class_description": " NIOS  bridge "}), headers=h)
    assert r.status_code == 201, r.text
    ac = r.json()["academic"]
    assert (ac["class_level"], ac["class_label"], ac["academic_year"], ac["other_class_description"]) == ("OTHER", "Other", Y, "NIOS bridge")
    assert roster(client, h, world["p1b1"])[r.json()["enrollment_id"]]["academic"]["class_level"] == "OTHER"


def test_class_per_provider_enrollment_not_on_the_global_identity(client, world, ct, login, make_staff, db_factory):
    from institute_common.core.roles import Role
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    for branch, who, cls in ((world["p1b1"], ct["pa"], "12th"), (world["p2b1"], pa2, "Dropper")):    # one session at a time
        r = client.post(f"{API}/branches/{branch}/enrollments", json={"student_id": "STU000005", "academic": {"class_level": cls, "academic_year": Y}},
                        headers=login(who))
        assert r.status_code == 201, r.text
    hs = login(ct["sa"])
    detail = client.get(f"{API}/students/STU000005", headers=hs).json()
    assert sorted(e["academic"]["class_label"] for e in detail["enrollments"] if e["status"] == "ACTIVE") == ["12th", "Dropper"]
    assert "class_level" not in detail["profile"]                       # the global identity carries no Class


def test_profile_update_is_partial_never_clears_and_is_recorded(client, world, ct, login, db_factory):
    from institute_common.models.academics import EnrollmentEvent
    h = login(ct["pa"])
    url = f"{API}/enrollments/e1/academic-profile"
    assert client.patch(url, json={"academic_year": Y}, headers=h).status_code == 422          # no profile yet: needs both
    assert client.patch(url, json={"class_level": "11th", "academic_year": Y}, headers=h).json()["class_label"] == "11th"
    r = client.patch(url, json={"academic_year": "2027-2028"}, headers=h).json()
    assert (r["class_level"], r["academic_year"]) == ("11TH", "2027-2028")                    # omitted class kept
    assert client.patch(url, json={"class_level": None}, headers=h).status_code == 422          # cannot be cleared
    assert client.patch(url, json={"class_level": "Other"}, headers=h).status_code == 422      # Other needs its description
    assert client.patch(url, json={"class_level": "Other", "other_class_description": "Foundation"}, headers=h).json()["other_class_description"] == "Foundation"
    assert client.patch(url, json={"class_level": "12th"}, headers=h).json()["other_class_description"] is None   # leaving Other drops it
    assert client.patch(url, json={"academic_year": "2027"}, headers=h).status_code == 422
    with db_factory() as db:
        reasons = [e.reason for e in db.query(EnrollmentEvent).filter_by(enrollment_id="e1", event_type="ACADEMIC_PROFILE_SET").order_by(EnrollmentEvent.occurred_at)]
    assert reasons[0] == f"Class 11th, {Y}" and reasons[-1] == "Class Other (Foundation), 2027-2028 -> Class 12th, 2027-2028"
    actions = [x["action"] for x in client.get(f"{API}/audit-logs?page_size=100", headers=login(ct["sa"])).json()["items"]]
    assert "enrollment.academic_profile" in actions


def test_only_enrollment_managers_of_that_branch_edit_profiles(client, world, ct, login, make_staff):
    from institute_common.core.roles import Role
    _, other_pa = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    _, ba2 = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    body = {"class_level": "11th", "academic_year": Y}
    for who in (ct["t_batch"], other_pa, ba2):
        assert client.patch(f"{API}/enrollments/e1/academic-profile", json=body, headers=login(who)).status_code == 403
    assert client.patch(f"{API}/enrollments/e1/academic-profile", json=body, headers=login(ct["ba"])).status_code == 200


def test_saving_the_profile_keeps_batches_and_subject_selections(client, world, ct, login, db_factory):
    from institute_common.models.academics import BatchSubject
    with db_factory() as db:
        for s in ("phy", "chem"):
            db.add(BatchSubject(batch_id=ct["batch_a"], subject_id=ct[s]))
        db.commit()
    h = login(ct["pa"])
    assert client.put(f"{API}/enrollments/e1/batches/{ct['batch_a']}", json={"subject_ids": [str(ct["phy"])]}, headers=h).status_code == 204
    assert client.patch(f"{API}/enrollments/e1/academic-profile", json={"class_level": "11th", "academic_year": Y}, headers=h).status_code == 200
    e = roster(client, h, world["p1b1"])["e1"]
    assert e["batch_ids"] == [str(ct["batch_a"])] and e["batch_subjects"] == {str(ct["batch_a"]): [str(ct["phy"])]}


def test_a_transfer_keeps_the_class_and_academic_year(client, world, ct, login):
    h = login(ct["pa"])
    assert client.patch(f"{API}/enrollments/e1/academic-profile", json={"class_level": "11th", "academic_year": Y}, headers=h).status_code == 200
    t = client.post(f"{API}/enrollments/e1/transfers", json={"to_branch_id": str(world["p1b2"]), "reason": "moved closer to home"}, headers=h).json()
    new = client.post(f"{API}/transfers/{t['transfer_id']}/approve", json={"note": "approved for the move"}, headers=h).json()["new_enrollment_id"]
    assert roster(client, h, world["p1b2"])[new]["academic"]["class_label"] == "11th"


# ------------------------------------------------------------------------------------------------ batch defaults

def test_batch_defaults_are_optional_validated_and_never_rewrite_enrollments(client, world, ct, login, db_factory):
    h = login(ct["pa"])
    url = f"{API}/branches/{world['p1b1']}/batches"
    for bad in ({"default_class_level": "13th"}, {"default_academic_year": "2026-2028"}, {"default_class_level": "Other"},
                {"default_class_level": "11th", "default_other_description": "x"}, {"default_other_description": "x"}):
        assert client.post(url, json={"name": "Bad defaults", **bad}, headers=h).status_code == 422, bad
    plain = client.post(url, json={"name": "No defaults"}, headers=h).json()
    assert plain["default_class_level"] is None and plain["default_academic_year"] is None
    b = client.post(url, json={"name": "NEET 11", "default_class_level": "11th", "default_academic_year": Y}, headers=h).json()
    assert (b["default_class_level"], b["default_class_label"], b["default_academic_year"]) == ("11TH", "11th", Y)
    listed = {x["batch_id"]: x for x in client.get(url, headers=h).json()["items"]}
    assert listed[b["batch_id"]]["default_class_label"] == "11th"
    prof(db_factory, "e1", "12TH")
    r = client.patch(f"{API}/batches/{b['batch_id']}", json={"default_class_level": "10th"}, headers=h).json()
    assert (r["default_class_level"], r["default_academic_year"]) == ("10TH", Y)                     # omitted year kept
    assert client.patch(f"{API}/batches/{b['batch_id']}", json={"name": "NEET 11 renamed"}, headers=h).json()["default_class_level"] == "10TH"
    r = client.patch(f"{API}/batches/{b['batch_id']}", json={"default_class_level": None, "default_academic_year": None}, headers=h).json()
    assert r["default_class_level"] is None and r["default_academic_year"] is None                  # explicit null clears defaults
    r = client.patch(f"{API}/batches/{b['batch_id']}", json={"default_class_level": "Other", "default_other_description": "Bridge"}, headers=h).json()
    assert (r["default_class_level"], r["default_other_description"]) == ("OTHER", "Bridge")
    assert roster(client, h, world["p1b1"])["e1"]["academic"]["class_level"] == "12TH"                # enrollments untouched
    assert client.patch(f"{API}/batches/{b['batch_id']}", json={"default_class_level": "11th"}, headers=login(ct["t_batch"])).status_code == 403
    actions = [x["action"] for x in client.get(f"{API}/audit-logs?page_size=100", headers=login(ct["sa"])).json()["items"]]
    assert "batch.update" in actions and "batch.create" in actions


# ------------------------------------------------------------------------------------------------ Class-aware exam audiences

@pytest.fixture()
def cls(ct, db_factory):
    """ct with Classes: STU000001 (e1, batch A) 11th; STU000004 (e4a, batch A) 12th; STU000002 (e2, batch B) 11th; STU000003 (e3, p1-b2)
    11th; STU000004's p1-b2 enrollment (e4b) has NO profile; STU000001 at provider p2 (e1p2) is in 12th."""
    for eid, code in (("e1", "11TH"), ("e4a", "12TH"), ("e2", "11TH"), ("e3", "11TH"), ("e1p2", "12TH")):
        prof(db_factory, eid, code)
    return ct


def test_class_matching_mismatch_and_missing_target(client, world, cls, login):
    h = login(cls["pa"])
    eleven = physics(client, h, world, cls, "Physics 11", "11th", batches=("batch_a",))
    twelve = physics(client, h, world, cls, "Physics 12", "12th", batches=("batch_a",))
    anyone = physics(client, h, world, cls, "Physics any class", batches=("batch_a",))
    for r in (eleven, twelve, anyone):
        assert r.status_code == 201, r.text
    assert students_of(client, login, cls["pa"], eleven.json()["exam_id"]) == ["STU000001"]
    assert students_of(client, login, cls["pa"], twelve.json()["exam_id"]) == ["STU000004"]
    assert students_of(client, login, cls["pa"], anyone.json()["exam_id"]) == ["STU000001", "STU000004"]   # no target: no Class filter


def test_whole_branch_and_provider_audiences_and_missing_profiles(client, world, cls, login):
    h = login(cls["pa"])
    r = client.post(f"{API}/tests/audience", json={**aud(world, cls, branches=("p1b1", "p1b2"), whole=("p1b1", "p1b2")), "target_class": "11th"}, headers=h)
    assert r.status_code == 200, r.text
    assert sorted(s["student_id"] for s in r.json()["students"]) == ["STU000001", "STU000002", "STU000003"]
    assert "1 in a different Class" in r.json()["class_note"]                       # STU000004 (12th in b1, no profile in b2)
    for kind, body in (("BRANCH", {"audience_type": "BRANCH"}), ("INSTITUTE", {"audience_type": "INSTITUTE"})):
        p = client.post(f"{API}/branches/{world['p1b1']}/audience-preview", json={**body, "target_class": "12th"}, headers=h).json()
        assert p["eligible_count"] == 1, (kind, p)                                   # only STU000004's 12th enrollment
    p = client.post(f"{API}/branches/{world['p1b2']}/audience-preview", json={"audience_type": "BRANCH", "target_class": "12th"}, headers=h).json()
    assert (p["eligible_count"], p["excluded_no_class"], p["excluded_other_class"]) == (0, 1, 1)   # e4b: no profile; e3: 11th


def test_other_and_legacy_targets_are_never_guessed(client, world, cls, login, db_factory):
    from institute_common.models.exams import Exam, ExamGenerationJob, ExamPlan
    from inst_exam.services import exam_generation as gen
    h = login(cls["pa"])
    for bad in ("Other", "Class 11", "XI"):
        assert physics(client, h, world, cls, f"Bad {bad}", bad, batches=("batch_a",)).status_code == 422, bad
        assert client.post(f"{API}/tests/audience", json={**aud(world, cls, batches=("batch_a",)), "target_class": bad}, headers=h).status_code == 422
    ok = physics(client, h, world, cls, "Legacy later", "11th", batches=("batch_a",)).json()
    with db_factory() as db:
        plan = db.get(ExamPlan, db.get(Exam, UUID(ok["exam_id"])).plan_id)
        plan.target_class = "Class 11"                               # a label stored before Classes were controlled
        db.commit()
        before = db.query(Exam).filter_by(plan_id=plan.plan_id).count()
        week = timedelta(days=7)                                     # the next window (generation is idempotent per window)
        job = gen.generate(db, plan.plan_id, start_at=plan.start_at + week, end_at=plan.end_at + week)
        assert job.status == "FAILED" and "not a recognised Class" in job.error
        assert db.query(Exam).filter_by(plan_id=plan.plan_id).count() == before            # nothing generated
        assert db.get(ExamGenerationJob, job.job_id).status == "FAILED"


def test_academic_year_text_never_matches_a_class(client, world, cls, login, db_factory):
    prof(db_factory, "e4a", "11TH", year="2012-2013")                # '12' in the year, Class 11th
    h = login(cls["pa"])
    r = physics(client, h, world, cls, "Physics 12 only", "12th", batches=("batch_a",))
    assert r.status_code == 422                                      # nobody in batch A is in 12th: publication refused
    assert "different Class" in r.text


def test_staff_listed_students_bypass_the_class(client, world, cls, login):
    h = login(cls["pa"])
    r = physics(client, h, world, cls, "Physics 12 + listed", "12th", batches=("batch_a",), students=("STU000001",))
    assert r.status_code == 201, r.text
    assert students_of(client, login, cls["pa"], r.json()["exam_id"]) == ["STU000001", "STU000004"]


def test_subject_selection_and_class_combine(client, world, cls, login, db_factory):
    from institute_common.models.academics import BatchSubject
    with db_factory() as db:
        for s in ("phy", "chem"):
            db.add(BatchSubject(batch_id=cls["batch_a"], subject_id=cls[s]))
        db.commit()
    prof(db_factory, "e4a", "11TH")                                  # both batch A students in 11th now
    h = login(cls["pa"])
    assert client.put(f"{API}/enrollments/e1/batches/{cls['batch_a']}", json={"subject_ids": [str(cls["phy"])]}, headers=h).status_code == 204
    both = publish(client, h, draft(world, cls, name="P+C 11", audience=aud(world, cls, batches=("batch_a",)), target_class="11th",
                                    subjects=[("phy", str(cls["phy_v1"])), ("chem", str(cls["chem_v1"]))]))
    assert students_of(client, login, cls["pa"], both.json()["exam_id"]) == ["STU000004"]   # STU000001 takes Physics only
    chem = publish(client, login(cls["pa"]), draft(world, cls, name="Chem 11", audience=aud(world, cls, batches=("batch_a",)), target_class="11th",
                                    subjects=[("chem", str(cls["chem_v1"]))]))
    assert chem.status_code == 201, chem.text
    assert students_of(client, login, cls["pa"], chem.json()["exam_id"]) == ["STU000004"]   # legacy no-selection membership: all subjects


def test_teachers_see_only_targeted_students_of_the_class(client, world, cls, tv, login):  # noqa: F811
    h = login(cls["pa"])
    r = physics(client, h, world, cls, "Physics 11 teachers", "11th", batches=("batch_a",))
    assert students_of(client, login, tv["t_phy_a"], r.json()["exam_id"]) == ["STU000001"]   # STU000004 (12th) was not targeted


def test_a_class_change_affects_only_future_generation(client, world, cls, login, db_factory):
    from institute_common.models.exams import Exam, ExamPlan, StudentExamAssignment
    from inst_exam.services import exam_generation as gen
    h = login(cls["pa"])
    first = physics(client, h, world, cls, "Physics 11 weekly", "11th", batches=("batch_a",)).json()
    assert client.patch(f"{API}/enrollments/e4a/academic-profile", json={"class_level": "11th"}, headers=h).status_code == 200
    with db_factory() as db:
        plan_id = db.get(Exam, UUID(first["exam_id"])).plan_id
        frozen = sorted(a.student_id for a in db.query(StudentExamAssignment).filter_by(exam_id=UUID(first["exam_id"])))
        plan = db.get(ExamPlan, plan_id)
        week = timedelta(days=7)
        job = gen.generate(db, plan_id, start_at=plan.start_at + week, end_at=plan.end_at + week)   # next week's window
        assert job.status in ("SUCCEEDED", "PARTIAL"), job.error
        nxt = sorted(a.student_id for a in db.query(StudentExamAssignment).filter_by(exam_id=job.exam_id))
        again = sorted(a.student_id for a in db.query(StudentExamAssignment).filter_by(exam_id=UUID(first["exam_id"])))
    assert frozen == again == ["STU000001"]                          # the generated exam is unchanged
    assert nxt == ["STU000001", "STU000004"]                         # the next generation sees the new Class


def test_a_global_test_occurrence_uses_its_class(client, world, cls, login, db_factory):
    from inst_exam.services.global_generation import resolve_submission
    from institute_common.models.academics import BatchSubject
    audience = aud(world, cls, batches=("batch_a",))
    with db_factory() as db:                          # Global Tests: a membership without a selection takes its batch's subjects
        db.add(BatchSubject(batch_id=cls["batch_a"], subject_id=cls["phy"]))
        db.commit()
    with db_factory() as db:
        eleven = resolve_submission(db, SimpleNamespace(audience=audience, subject_id=cls["phy"]), "11TH")
        unmatchable = resolve_submission(db, SimpleNamespace(audience=audience, subject_id=cls["phy"]), "!unmatchable")
    assert [r[0] for r in eleven] == ["STU000001"] and unmatchable == []
