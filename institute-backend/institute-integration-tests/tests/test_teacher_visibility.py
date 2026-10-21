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
