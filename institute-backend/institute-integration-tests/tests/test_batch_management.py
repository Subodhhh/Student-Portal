"""Batches after creation: edit, deactivate ("delete") and reactivate, and adding enrolled students. A batch is never physically removed."""
from uuid import UUID

from sqlalchemy import select

from institute_common.core.roles import Role
from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
from tests.test_academics_enrollment import ACAD, API, seeded, students  # noqa: F401


def _batch(client, h, branch, name="Morning"):
    r = client.post(f"{API}/branches/{branch}/batches", json={"name": name}, headers=h)
    assert r.status_code == 201, r.text
    return UUID(r.json()["batch_id"])


def _enroll(client, h, students, branch, sid="STU000001", email="a@x.io"):
    students.add(sid, "Asha", "Rao", email)
    r = client.post(f"{API}/branches/{branch}/enrollments", json={"student_id": sid, "academic": ACAD}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["enrollment_id"]


def _names(client, h, branch, **params):
    r = client.get(f"{API}/branches/{branch}/batches", params=params, headers=h)
    assert r.status_code == 200, r.text
    return [b["name"] for b in r.json()["items"]]


def test_delete_is_deactivation_and_keeps_memberships(client, world, seeded, students, make_staff, login, db_factory):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h, br = login(pa), world["p1b1"]
    bid, eid = _batch(client, h, br), _enroll(client, h, students, br)
    assert client.put(f"{API}/enrollments/{eid}/batches/{bid}", headers=h).status_code == 204
    assert client.patch(f"{API}/batches/{bid}", json={"name": "Morning A", "description": "Early group"}, headers=h).status_code == 200
    assert client.patch(f"{API}/batches/{bid}", json={"status": "INACTIVE"}, headers=h).status_code == 200

    assert _names(client, h, br) == ["batch-p1b1"]                          # gone from the normal list (the fixture's batch remains)
    assert _names(client, h, br, status="INACTIVE") == ["Morning A"]
    with db_factory() as db:
        assert db.get(Batch, bid).status == "INACTIVE"                      # the row and the membership survive
        assert db.scalar(select(EnrollmentBatch.enrollment_id).where(EnrollmentBatch.batch_id == bid)) == eid

    # nothing new on an inactive batch
    assert client.post(f"{API}/batches/{bid}/members", json={"enrollment_ids": [eid]}, headers=h).status_code == 422
    assert client.put(f"{API}/enrollments/{eid}/batches/{bid}", headers=h).status_code == 422
    assert client.put(f"{API}/batches/{bid}/subjects/{seeded['phy']}", headers=h).status_code == 409

    assert client.patch(f"{API}/batches/{bid}", json={"status": "ACTIVE"}, headers=h).status_code == 200
    counts = {b["name"]: b["student_count"] for b in client.get(f"{API}/branches/{br}/batches", headers=h).json()["items"]}
    assert counts == {"Morning A": 1, "batch-p1b1": 0}                      # back, with its students


def test_adding_students_reports_duplicates_and_never_crosses_a_branch(client, world, seeded, students, make_staff, login, db_factory):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h, br = login(pa), world["p1b1"]
    bid = _batch(client, h, br)
    e1 = _enroll(client, h, students, br)
    e2 = _enroll(client, h, students, br, "STU000002", "b@x.io")
    other = _enroll(client, h, students, world["p1b2"], "STU000003", "c@x.io")
    url = f"{API}/batches/{bid}/members"

    r = client.post(url, json={"enrollment_ids": [e1, e1, other, "ENR-NOPE"]}, headers=h)
    assert r.status_code == 200 and r.json() == {"added": [e1], "already_members": [], "rejected": [other, "ENR-NOPE"]}
    r = client.post(url, json={"enrollment_ids": [e1, e2]}, headers=h)
    assert r.json() == {"added": [e2], "already_members": [e1], "rejected": []}

    with db_factory() as db:
        assert sorted(db.scalars(select(EnrollmentBatch.enrollment_id).where(EnrollmentBatch.batch_id == bid))) == sorted([e1, e2])
        assert len(db.scalars(select(Enrollment.enrollment_id)).all()) == 3     # memberships only: no new enrollment
    assert {b["name"]: b["student_count"] for b in client.get(f"{API}/branches/{br}/batches", headers=h).json()["items"]}["Morning"] == 2
    ids = lambda **p: [x["enrollment_id"] for x in client.get(f"{API}/branches/{br}/enrollments", params=p, headers=h).json()["items"]]  # noqa: E731
    assert ids(not_in_batch=bid) == []
    assert sorted(ids(batch_id=bid)) == sorted([e1, e2])
    assert ids(q="000002") == [e2]


def test_batch_operations_stay_in_the_callers_scope(client, world, seeded, students, make_staff, login):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, sibling = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    _, foreign = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(pa)
    bid, eid = _batch(client, h, world["p1b1"]), _enroll(client, h, students, world["p1b1"])

    for code in (sibling, foreign):
        hx = login(code)
        assert client.patch(f"{API}/batches/{bid}", json={"status": "INACTIVE"}, headers=hx).status_code == 403
        assert client.post(f"{API}/batches/{bid}/members", json={"enrollment_ids": [eid]}, headers=hx).status_code == 403
        assert client.get(f"{API}/branches/{world['p1b1']}/batches", headers=hx).status_code == 403
    for code in (ba, sa):
        hx = login(code)
        assert client.patch(f"{API}/batches/{bid}", json={"name": f"By {code}"}, headers=hx).status_code == 200
        assert client.post(f"{API}/batches/{bid}/members", json={"enrollment_ids": [eid]}, headers=hx).status_code == 200
