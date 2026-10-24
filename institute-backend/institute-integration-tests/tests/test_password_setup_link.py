"""Staff-initiated "Send password setup link": staff who manage the enrollment (enrollment.manage for its provider/branch) ask
the Student service to email the student a setup link; the per-student limit (3/hour, enforced there) comes back as 429; every
attempt is audited; the token never reaches the Institute side. Fixtures: tc/ct (e1 = STU000001 ACTIVE in p1-b1; e5 inactive)."""
from tests.test_create_test import ct  # noqa: F401  (fixture)
from tests.test_test_creation import tc  # noqa: F401  (fixture)

API = "/api/v1"


def send(client, h, eid="e1"):
    return client.post(f"{API}/enrollments/{eid}/password-setup-link", headers=h)


def audit_rows(client, login, ct):
    items = client.get(f"{API}/audit-logs?page_size=100", headers=login(ct["sa"])).json()["items"]
    return [x for x in items if x["action"] == "student.password_setup_link"]


def test_staff_who_manage_the_enrollment_can_send_a_setup_link(client, ct, login):  # noqa: F811
    r = send(client, login(ct["pa"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"student_id": "STU000001", "expires_at": "2026-10-24T12:00:00+00:00"}     # no token in the response
    assert send(client, login(ct["ba"])).status_code == 200                                         # the branch's own admin


def test_others_are_refused(client, world, ct, login, make_staff):  # noqa: F811
    from institute_common.core.roles import Role
    _, other_provider = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    _, other_branch = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    for who in (ct["t_batch"], other_provider, other_branch):
        assert send(client, login(who)).status_code == 403
    assert audit_rows(client, login, ct) == []                                                     # nothing was sent


def test_an_inactive_enrollment_is_refused(client, ct, login):  # noqa: F811
    assert send(client, login(ct["pa"]), eid="e5").status_code == 409


def test_the_per_student_limit_is_reported_and_every_attempt_is_audited(client, ct, login):  # noqa: F811
    codes = [send(client, login(ct["pa"])).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    rows = audit_rows(client, login, ct)
    assert sorted(r["outcome"] for r in rows) == ["FAILURE", "SUCCESS", "SUCCESS", "SUCCESS"]
    assert all(r["target_id"] == "STU000001" for r in rows)
    assert not any("token" in str(r.get("details") or {}) for r in rows)                            # never a token in the audit


def test_student_service_outage_is_503(client, ct, login):  # noqa: F811
    from institute_common.clients.student_client import get_student_client
    get_student_client().down = True
    try:
        assert send(client, login(ct["pa"])).status_code == 503
    finally:
        get_student_client().down = False
