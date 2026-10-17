"""Student directory (global identities + scoped enrollments), audit-log filters, last sign-in, provider counts and the
last-Super-Admin guard."""
from datetime import timedelta

import pytest

from institute_common.core.roles import Role
from institute_common.database import utcnow
from tests.test_academics_enrollment import FakeStudents

API = "/api/v1"


@pytest.fixture()
def people(client, world, db_factory):
    from institute_common.clients.student_client import set_student_client
    from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
    fake = FakeStudents(db_factory)
    fake.add("STU000001", "Riya", "Patil", "riya@example.com", "9000000001")
    fake.add("STU000002", "Aman", "Shah", "aman@example.com")
    fake.add("STU000003", "Neel", "Rao", "neel@example.com")
    set_student_client(fake)
    with db_factory() as db:
        batch = Batch(branch_id=world["p1b1"], name="NEET 2027")
        db.add(batch)
        db.flush()
        for eid, sid, prov, br in (("e1", "STU000001", "p1", "p1b1"), ("e2", "STU000001", "p2", "p2b1"), ("e3", "STU000002", "p1", "p1b2"),
                                   ("e4", "STU000003", "p2", "p2b2")):
            db.add(Enrollment(enrollment_id=eid, provider_id=world[prov], branch_id=world[br], student_id=sid))
        db.flush()
        db.add(EnrollmentBatch(enrollment_id="e1", batch_id=batch.batch_id))
        db.commit()
    yield fake
    set_student_client(None)


def test_one_row_per_global_student_with_only_the_enrollments_in_scope(client, world, people, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    d = client.get(f"{API}/students", headers=login(sa)).json()
    assert [x["student_id"] for x in d["items"]] == ["STU000001", "STU000002", "STU000003"]
    riya = d["items"][0]
    assert riya["name"] == "Riya Patil" and len(riya["enrollments"]) == 2           # one identity, two providers
    assert {e["provider_name"] for e in riya["enrollments"]} == {"Provider p1", "Provider p2"}
    assert next(e for e in riya["enrollments"] if e["branch_name"] == "p1-b1")["batches"] == ["NEET 2027"]

    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    d = client.get(f"{API}/students", headers=login(pa)).json()
    assert [x["student_id"] for x in d["items"]] == ["STU000001", "STU000002"]
    assert [e["provider_name"] for e in d["items"][0]["enrollments"]] == ["Provider p1"]   # p2's enrollment of the same student is not shown

    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    h = login(ba)
    assert [x["student_id"] for x in client.get(f"{API}/students", headers=h).json()["items"]] == ["STU000002"]
    assert client.get(f"{API}/students/STU000001", headers=h).status_code == 404          # outside the branch: not revealed


def test_search_by_id_email_or_phone_and_details(client, world, people, make_staff, login):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    ids = lambda q: [x["student_id"] for x in client.get(f"{API}/students?q={q}", headers=h).json()["items"]]  # noqa: E731
    assert ids("STU0000") == ["STU000001", "STU000002"]
    assert ids("aman@example.com") == ["STU000002"]
    assert ids("9000000001") == ["STU000001"]
    assert ids("neel@example.com") == []                                                  # enrolled only with p2
    assert ids("Riya") == []                                                              # names are not searchable here (no 422)
    assert client.get(f"{API}/students?q=Riya%20Patil", headers=h).status_code == 422     # free text with spaces is still unsupported
    detail = client.get(f"{API}/students/STU000001", headers=h).json()
    assert detail["profile"]["email"] == "riya@example.com" and [e["provider_name"] for e in detail["enrollments"]] == ["Provider p1"]
    assert detail["exams"] == []
    _, t = make_staff([(Role.TEACHER, world["p1"], None)])
    assert client.get(f"{API}/students", headers=login(t)).status_code == 403


def test_audit_filters_and_actor_names(client, world, make_staff, login, db_factory):
    from institute_common.models.staff import AuditLog
    sid, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    other, other_code = make_staff([(Role.ADMIN, None, None)])
    now = utcnow()
    with db_factory() as db:
        db.add_all([AuditLog(action="provider.update", outcome="SUCCESS", actor_staff_id=other, provider_id=world["p1"], target_type="provider",
                             target_id=str(world["p1"]), occurred_at=now - timedelta(days=3)),
                    AuditLog(action="provider.update", outcome="SUCCESS", actor_staff_id=other, provider_id=world["p2"], target_type="provider",
                             target_id=str(world["p2"]), occurred_at=now - timedelta(hours=1))])
        db.commit()
    h = login(sa)
    rows = client.get(f"{API}/audit-logs?action=provider.update", headers=h).json()["items"]
    assert len(rows) == 2 and rows[0]["actor_code"] == other_code and rows[0]["provider_name"] == "Provider p2"
    assert client.get(f"{API}/audit-logs?action=provider.update&provider_id={world['p1']}", headers=h).json()["total"] == 1
    since = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert client.get(f"{API}/audit-logs?action=provider.update&since={since}", headers=h).json()["total"] == 1
    assert client.get(f"{API}/audit-logs?action=provider.update&until={since}", headers=h).json()["total"] == 1
    assert client.get(f"{API}/audit-logs?actor_code={other_code.lower()}", headers=h).json()["total"] == 2
    assert client.get(f"{API}/audit-logs?target_type=provider&target_id={world['p2']}", headers=h).json()["total"] == 1
    assert client.get(f"{API}/audit-logs?actor_code=STF999999", headers=h).json()["total"] == 0


def test_last_sign_in_comes_from_the_audit_log(client, world, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    tid, tcode = make_staff([(Role.TEACHER, world["p1"], None)])
    h = login(sa)
    assert client.get(f"{API}/staff/{tid}", headers=h).json()["last_login_at"] is None
    login(tcode)
    h = login(sa)
    assert client.get(f"{API}/staff/{tid}", headers=h).json()["last_login_at"] is not None


def test_provider_directory_counts(client, world, make_staff, login, db_factory):
    from institute_common.models.org import Enrollment
    make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    make_staff([(Role.TEACHER, world["p1"], None), (Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])   # one person, two roles
    with db_factory() as db:
        db.add_all([Enrollment(enrollment_id="x1", provider_id=world["p1"], branch_id=world["p1b1"], student_id="STU000001"),
                    Enrollment(enrollment_id="x2", provider_id=world["p1"], branch_id=world["p1b2"], student_id="STU000001"),
                    Enrollment(enrollment_id="x3", provider_id=world["p1"], branch_id=world["p1b2"], student_id="STU000002", status="INACTIVE")])
        db.commit()
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(sa)
    p1 = next(x for x in client.get(f"{API}/providers", headers=h).json()["items"] if x["name"] == "Provider p1")
    assert (p1["branch_count"], p1["staff_count"], p1["student_count"]) == (2, 2, 1)
    assert client.get(f"{API}/providers/{world['p1']}", headers=h).json()["student_count"] == 1


def test_the_last_active_super_admin_cannot_be_removed(db_factory, make_staff):
    from fastapi import HTTPException

    from inst_identity.routes.staff import ensure_not_last_super_admin
    only, _ = make_staff([(Role.SUPER_ADMIN, None, None)])
    with db_factory() as db, pytest.raises(HTTPException) as exc:
        ensure_not_last_super_admin(db, only)
    assert exc.value.detail["code"] == "LAST_SUPER_ADMIN"
    make_staff([(Role.SUPER_ADMIN, None, None)], active=False)        # an inactive second Super Admin does not count
    with db_factory() as db, pytest.raises(HTTPException):
        ensure_not_last_super_admin(db, only)
    make_staff([(Role.SUPER_ADMIN, None, None)])
    with db_factory() as db:
        ensure_not_last_super_admin(db, only)                         # another active Super Admin remains


def test_super_admins_cannot_remove_themselves(client, make_staff, login):
    me, code = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(code)
    assert client.post(f"{API}/staff/{me}/deactivate", json={"reason": "leaving the platform"}, headers=h).status_code == 403
