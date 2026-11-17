"""Teacher visibility of tests: a teacher sees a test when at least one TARGETED student is in their teaching scope for at least one
of the test's subjects - and then only those students. Covers multiple subjects, multiple batches, targeted versus non-targeted
students, tests not generated yet, and that nothing else (creation, publication, other students' data) is granted."""
import uuid

import pytest

from institute_common.core.roles import Role
from tests.test_create_test import aud, ct, draft, publish  # noqa: F401  (fixtures and helpers)
from tests.test_exams import FakeResults
from tests.test_test_creation import tc  # noqa: F401  (fixture)
from tests.test_weekly_tests import create as create_series, series

API = "/api/v1"


@pytest.fixture()
def tv(client, world, ct, make_staff, db_factory):  # noqa: F811
    """ct (p1-b1: batch A = STU000001, STU000004; batch B = STU000002; p1-b2: batch C = STU000003, STU000004) plus teachers:
    phy_a (Physics, batch A), phy_b (Physics, batch B), chem_a (Chemistry, batch A), bio (Biology, whole p1-b1), phy_b2 (Physics, p1-b2)."""
    from institute_common.models.academics import ProviderSubject, Subject, TeacherSubjectAssignment
    c = dict(ct)
    with db_factory() as db:
        bio = Subject(code="BIO", name="Biology")
        db.add(bio)
        db.flush()
        db.add(ProviderSubject(provider_id=world["p1"], subject_id=bio.subject_id))
        c["bio"] = bio.subject_id
        db.commit()
    for key, subject, branch, batch in (("phy_a", "phy", "p1b1", "batch_a"), ("phy_b", "phy", "p1b1", "batch_b"), ("chem_a", "chem", "p1b1", "batch_a"),
                                        ("bio", "bio", "p1b1", None), ("phy_b2", "phy", "p1b2", None)):
        sid, c[f"t_{key}"] = make_staff([(Role.TEACHER, world["p1"], None)])
        with db_factory() as db:
            db.add(TeacherSubjectAssignment(staff_id=sid, provider_id=world["p1"], branch_id=world[branch], subject_id=c[subject],
                                            batch_id=c[batch] if batch else None))
            db.commit()
    return c


def physics_chemistry(client, login, world, tv, name, **audience):
    r = publish(client, login(tv["pa"]), draft(world, tv, name=name, audience=aud(world, tv, **audience),
                                                subjects=[("phy", str(tv["phy_v1"])), ("chem", str(tv["chem_v1"]))]))
    assert r.status_code == 201, r.text
    return r.json()


def names(client, login, who):
    return sorted(x["name"] for x in client.get(f"{API}/tests", headers=login(who)).json()["items"])


def students_of(client, login, who, exam_id):
    return sorted(a["student_id"] for a in client.get(f"{API}/exams/{exam_id}/assignments", headers=login(who)).json()["items"])


def test_one_matching_subject_is_enough_and_unrelated_subjects_see_nothing(client, world, tv, login):
    t = physics_chemistry(client, login, world, tv, "Physics and Chemistry", batches=("batch_a",))
    assert names(client, login, tv["t_phy_a"]) == ["Physics and Chemistry"]        # teaches Physics only, to batch A
    assert names(client, login, tv["t_chem_a"]) == ["Physics and Chemistry"]
    assert names(client, login, tv["t_bio"]) == []                                  # Biology: no matching subject
    for who in ("t_phy_a", "t_chem_a"):
        assert client.get(f"{API}/exams/{t['exam_id']}", headers=login(tv[who])).status_code == 200
    for who in ("t_bio", "t_phy_b", "t_phy_b2"):
        assert client.get(f"{API}/exams/{t['exam_id']}", headers=login(tv[who])).status_code == 403
        assert client.get(f"{API}/exams/{t['exam_id']}/assignments", headers=login(tv[who])).status_code == 403


def test_the_same_subject_in_another_batch_or_branch_does_not_see_the_test(client, world, tv, login):
    physics_chemistry(client, login, world, tv, "Batch A only", batches=("batch_a",))
    assert names(client, login, tv["t_phy_b"]) == []
    assert names(client, login, tv["t_phy_b2"]) == []     # STU000004 is also in batch C, but was targeted through batch A in p1-b1


def test_multiple_batches_show_each_teacher_only_their_own_students(client, world, tv, login, db_factory):
    from institute_common.clients.result_client import set_result_client
    t = physics_chemistry(client, login, world, tv, "Batches A and B", batches=("batch_a", "batch_b"))
    assert names(client, login, tv["t_phy_a"]) == names(client, login, tv["t_phy_b"]) == ["Batches A and B"]
    assert students_of(client, login, tv["t_phy_a"], t["exam_id"]) == ["STU000001", "STU000004"]
    assert students_of(client, login, tv["t_phy_b"], t["exam_id"]) == ["STU000002"]
    assert students_of(client, login, tv["pa"], t["exam_id"]) == ["STU000001", "STU000002", "STU000004"]   # admins unchanged
    detail = client.get(f"{API}/exams/{t['exam_id']}", headers=login(tv["t_phy_b"])).json()
    assert detail["assigned_students"] == 1                                        # counts restricted to the teacher's students
    set_result_client(FakeResults([{"student_id": s, "obtained_marks": m, "total_marks": 20, "accuracy": 50.0}
                                   for s, m in (("STU000001", 8), ("STU000002", 12), ("STU000004", 4))]))
    try:
        mine = client.get(f"{API}/exams/{t['exam_id']}/results", headers=login(tv["t_phy_b"])).json()
        assert [r["student_id"] for r in mine["results"]] == ["STU000002"] and mine["summary"]["assigned"] == 1
        everyone = client.get(f"{API}/exams/{t['exam_id']}/results", headers=login(tv["ba"])).json()
        assert len(everyone["results"]) == 3
    finally:
        set_result_client(None)


def test_targeted_versus_non_targeted_students(client, world, tv, login):
    """A test for one individual student of batch B: the batch-A teacher has students in that branch, but none is targeted."""
    physics_chemistry(client, login, world, tv, "Only STU000002", students=("STU000002",))
    assert names(client, login, tv["t_phy_a"]) == []
    assert names(client, login, tv["t_phy_b"]) == ["Only STU000002"]


def test_branch_exam_list_follows_the_same_rule(client, world, tv, login):
    t = physics_chemistry(client, login, world, tv, "Listed for batch A", batches=("batch_a",))
    listed = lambda who: [e["exam_id"] for e in client.get(f"{API}/branches/{world['p1b1']}/exams", headers=login(tv[who])).json()["items"]]  # noqa: E731
    assert listed("t_phy_a") == [t["exam_id"]]
    assert listed("t_phy_b") == [] and listed("t_bio") == []
    assert listed("ba") == [t["exam_id"]]


def test_tests_not_generated_yet_use_the_audience_resolved_now(client, world, tv, login):
    body = series(world, tv, audience=aud(world, tv, batches=("batch_b",)))
    assert create_series(client, login(tv["pa"]), body).status_code == 201
    assert names(client, login, tv["t_phy_b"]) and set(names(client, login, tv["t_phy_b"])) == {"Saturday Physics"}
    assert names(client, login, tv["t_phy_a"]) == []
    assert [s["name"] for s in client.get(f"{API}/test-series", headers=login(tv["t_phy_b"])).json()["items"]] == ["Saturday Physics"]
    assert client.get(f"{API}/test-series", headers=login(tv["t_phy_a"])).json()["items"] == []


def test_visibility_grants_nothing_else(client, world, tv, login):
    t = physics_chemistry(client, login, world, tv, "Read only", batches=("batch_a",))
    h = login(tv["t_phy_a"])
    assert publish(client, h, draft(world, tv)).status_code == 403
    assert client.post(f"{API}/exams/{t['exam_id']}/cancel", json={"reason": "not allowed"}, headers=h).status_code == 403
    assert client.post(f"{API}/exams/{t['exam_id']}/publish", headers=h).status_code == 403
    assert client.post(f"{API}/global-test-contributions/{uuid.uuid4()}/approve", json={}, headers=h).status_code == 403
    assert "test.create" not in client.get(f"{API}/auth/me", headers=h).json()["permissions"]


def test_bulk_teacher_scope_equals_the_per_plan_rule_with_a_fixed_number_of_statements(client, world, tv, login, db_factory, make_staff):
    """The Scheduled Tests list decides every plan's teacher visibility at once (teacher_scope.plans_students): same students as the
    per-plan rule (plan_students) for every teacher and plan, including a plan with no exam yet and a teacher at two providers, the
    same list across pages, and a statement count that does not grow with the number of plans that have an exam."""
    from sqlalchemy import event, select
    from institute_common.core.security import Principal, load_principal
    from institute_common.models.academics import TeacherSubjectAssignment
    from institute_common.models.exams import Exam, ExamPlan
    from institute_common.models.staff import Staff
    from inst_exam.services import teacher_scope
    for i, batch in enumerate(("batch_a", "batch_b", "batch_a")):
        physics_chemistry(client, login, world, tv, f"Test {i}", batches=(batch,))
    h = login(tv["pa"])
    assert create_series(client, h, series(world, tv, audience=aud(world, tv, batches=("batch_b",)))).status_code == 201
    # plans without an exam for every audience shape: whole branch, several batches, listed students, a mix across both branches, and
    # class targets (only STU000001's enrollment is in Class 11, STU000002's in Class 12, the others have no profile)
    from institute_common.models.academics import EnrollmentAcademicProfile
    from institute_common.models.org import Enrollment
    with db_factory() as db:
        for sid, code in (("STU000001", "11TH"), ("STU000002", "12TH")):
            for (eid,) in db.execute(select(Enrollment.enrollment_id).where(Enrollment.student_id == sid, Enrollment.provider_id == world["p1"])):
                db.add(EnrollmentAcademicProfile(enrollment_id=eid, class_level=code, academic_year="2026-2027"))
        db.commit()
    shapes = [(aud(world, tv, whole=("p1b1",)), None), (aud(world, tv, batches=("batch_a", "batch_b")), None),
              (aud(world, tv, students=("STU000002", "STU000004")), None),
              (aud(world, tv, branches=("p1b1", "p1b2"), whole=("p1b2",), batches=("batch_b",), students=("STU000001",)), None),
              (aud(world, tv, whole=("p1b1",)), "11th"), (aud(world, tv, batches=("batch_a", "batch_b")), "12th")]
    for i, (audience, cls) in enumerate(shapes):
        body = series(world, tv, audience=audience, name=f"Weekly {i}", **({"target_class": cls} if cls else {}))
        r = create_series(client, h, body, key=f"weekly-bulk-{i:04d}")
        assert r.status_code == 201, r.text
    # a teacher at two providers who teaches Physics only at the other provider: nothing of p1's tests is theirs
    two_id, two = make_staff([(Role.TEACHER, world["p1"], None), (Role.TEACHER, world["p2"], None)])
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=two_id, provider_id=world["p2"], branch_id=world["p2b1"], subject_id=tv["phy"]))
        db.commit()
    teachers = [tv[k] for k in ("t_phy_a", "t_phy_b", "t_chem_a", "t_bio", "t_phy_b2")] + [two]
    with db_factory() as db:
        plans = list(db.scalars(select(ExamPlan).order_by(ExamPlan.created_at)))
        with_exam = [pl for pl in plans if db.scalar(select(Exam.exam_id).where(Exam.plan_id == pl.plan_id).limit(1))]
        assert len(with_exam) >= 3 and len(with_exam) < len(plans)                   # some plans have no exam yet
        statements = []
        event.listen(db.get_bind(), "before_cursor_execute", lambda *a: statements.append(1))
        # the bulk inputs + provider snapshot give every plan without an exam exactly the audience gen.eligible_students resolves alone
        from inst_exam.services import exam_generation as gen
        no_exam = [pl for pl in plans if pl not in with_exam]
        inputs = gen.eligible_inputs(db, [pl.plan_id for pl in no_exam])
        plain = {pl.plan_id: gen.eligible_students(db, pl) for pl in no_exam}
        db.info["audience_snapshot"] = True
        assert {pl.plan_id: gen.eligible_students(db, pl, inputs=inputs[pl.plan_id]) for pl in no_exam} == plain
        assert any(plain.values()) and len(no_exam) >= 5
        del db.info["audience_snapshot"]
        for code in teachers:
            staff_id = db.scalar(select(Staff.staff_id).where(Staff.staff_code == code))
            p = Principal(*load_principal(db, staff_id), "s", "", "127.0.0.1")
            bulk = teacher_scope.plans_students(db, p, plans)
            assert bulk == {pl.plan_id: teacher_scope.plan_students(db, p, pl) for pl in plans}, code
            if code == two:
                assert not any(bulk.values())                                        # no cross-provider exposure
            for subset in (with_exam, no_exam):                                      # fixed, whatever the number of plans
                del statements[:]
                teacher_scope.plans_students(db, p, subset[:1])
                one = len(statements)
                del statements[:]
                teacher_scope.plans_students(db, p, subset)
                # only an audience listing students reads them per plan (resolve_audience's STUDENTS rule has no snapshot)
                listed = sum(1 for pl in subset[1:] if inputs.get(pl.plan_id, {}).get("students"))
                assert len(statements) <= one + listed, (code, one, listed, len(statements))
            assert "audience_snapshot" not in db.info                                # the opt-in never outlives the check
    for code in teachers:                                                            # pages of 2 make up the whole list, in order
        h = login(code)
        whole = [x["name"] for x in client.get(f"{API}/tests?page_size=100", headers=h).json()["items"]]
        paged = []
        for page in range(1, 50):
            chunk = [x["name"] for x in client.get(f"{API}/tests?page={page}&page_size=2", headers=h).json()["items"]]
            if not chunk:
                break
            paged += chunk
        assert paged == whole, code


def test_prefetched_teacher_exam_checks_equal_the_per_exam_rule(client, world, tv, login, db_factory, make_staff):
    """exam_access's teacher check (teacher_scope.exam_students) for a list of exams reads its inputs once (prefetch_exam_students): the
    same students for every teacher, exam and branch filter as the per-exam reads, nothing read per exam afterwards, a statement count
    that does not grow with the number of exams, and unchanged refusals (403 + authz.denied) for an exam a teacher may not read."""
    from sqlalchemy import event, func, select
    from institute_common.core.security import Principal, load_principal
    from institute_common.models.academics import TeacherSubjectAssignment
    from institute_common.models.staff import AuditLog
    from institute_common.models.exams import Exam
    from institute_common.models.staff import Staff
    from inst_exam.services import teacher_scope
    made = [physics_chemistry(client, login, world, tv, f"Exam {i}", batches=batches)
            for i, batches in enumerate((("batch_a",), ("batch_b",), ("batch_a", "batch_b")))]
    two_id, two = make_staff([(Role.TEACHER, world["p1"], None), (Role.TEACHER, world["p2"], None)])
    multi_id, multi = make_staff([(Role.TEACHER, world["p1"], None)])                   # two subjects, two branches
    with db_factory() as db:
        db.add(TeacherSubjectAssignment(staff_id=two_id, provider_id=world["p2"], branch_id=world["p2b1"], subject_id=tv["phy"]))
        db.add(TeacherSubjectAssignment(staff_id=multi_id, provider_id=world["p1"], branch_id=world["p1b1"], subject_id=tv["chem"], batch_id=tv["batch_b"]))
        db.add(TeacherSubjectAssignment(staff_id=multi_id, provider_id=world["p1"], branch_id=world["p1b2"], subject_id=tv["phy"]))
        db.commit()
    teachers = [tv[k] for k in ("t_phy_a", "t_phy_b", "t_chem_a", "t_bio", "t_phy_b2")] + [two, multi]
    branches = (None, world["p1b1"], world["p1b2"], world["p2b1"])
    seen = set()
    for code in teachers:
        with db_factory() as db:
            staff_id = db.scalar(select(Staff.staff_id).where(Staff.staff_code == code))
            p = Principal(*load_principal(db, staff_id), "s", "", "127.0.0.1")
            exams = list(db.scalars(select(Exam).order_by(Exam.created_at)))
            plain = {(e.exam_id, b): teacher_scope.exam_students(db, p, e, b) for e in exams for b in branches}
        with db_factory() as db:
            p = Principal(*load_principal(db, staff_id), "s", "", "127.0.0.1")
            exams = list(db.scalars(select(Exam).order_by(Exam.created_at)))
            statements = []
            count = lambda *a, _seen=statements: _seen.append(1)  # noqa: E731
            event.listen(db.get_bind(), "before_cursor_execute", count)
            teacher_scope.prefetch_exam_students(db, p, exams[:1])
            one = len(statements)
            db.info.pop("teacher_exam_inputs"); db.info.pop("teacher_exam_memberships", None)
            del statements[:]
            teacher_scope.prefetch_exam_students(db, p, exams)
            assert len(statements) <= one + 1, (code, one, len(statements))               # +1: memberships once any exam is taught
            del statements[:]
            assert {(e.exam_id, b): teacher_scope.exam_students(db, p, e, b) for e in exams for b in branches} == plain, code
            assert statements == []                                                        # answered from the request's reads
            event.remove(db.get_bind(), "before_cursor_execute", count)
        if code == two:
            assert not any(plain.values())                                                 # no cross-provider exposure
        seen |= {k for k, v in plain.items() if v}
    assert seen                                                                            # some teacher does see students
    # refusals are unchanged: an exam the teacher may not read answers 403 and is audited, also after a list prefetched it
    h = login(tv["t_bio"])
    assert client.get(f"{API}/exams", headers=h).json()["items"] == []
    with db_factory() as db:
        before = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "authz.denied"))
    assert client.get(f"{API}/exams/{made[0]['exam_id']}", headers=h).status_code == 403
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "authz.denied")) == before + 1
    # every list a teacher reads is the same across page sizes, in order
    for code in teachers:
        h = login(code)
        for path in ("/exams", f"/branches/{world['p1b1']}/exams", "/tests"):
            full = client.get(f"{API}{path}?page_size=100", headers=h)
            if full.status_code != 200:                                    # e.g. a branch the teacher does not teach in: refused
                assert full.status_code == 403 and client.get(f"{API}{path}?page=1&page_size=2", headers=h).status_code == 403, (code, path)
                continue
            whole = [x.get("exam_id") or x.get("plan_id") for x in full.json()["items"]]
            paged = []
            for page in range(1, 50):
                chunk = [x.get("exam_id") or x.get("plan_id") for x in client.get(f"{API}{path}?page={page}&page_size=2", headers=h).json()["items"]]
                if not chunk:
                    break
                paged += chunk
            assert paged == whole, (code, path)
