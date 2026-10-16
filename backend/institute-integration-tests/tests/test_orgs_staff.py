import re

from institute_common.core.roles import Role
from tests.conftest import ORIGIN, PASSWORD

API = "/api/v1"


def j(r):
    return r.json()


def test_provider_crud_permissions(client, world, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(sa)
    r = client.post(f"{API}/providers", json={"name": "Aakash Test", "email": "new@example.com"}, headers=h)
    assert r.status_code == 201
    assert client.post(f"{API}/providers", json={"name": "Dup", "email": "new@example.com"}, headers=h).status_code == 409
    assert client.post(f"{API}/providers", json={"name": "Extra", "email": "e@example.com", "role": "x"}, headers=h).status_code == 422  # extra=forbid
    assert client.get(f"{API}/providers", headers=h).json()["total"] == 3


def test_provider_admin_cannot_create_provider_or_see_other_provider(client, world, make_staff, login):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    assert client.post(f"{API}/providers", json={"name": "Nope", "email": "n@example.com"}, headers=h).status_code == 403
    listing = client.get(f"{API}/providers", headers=h).json()
    assert [i["provider_id"] for i in listing["items"]] == [str(world["p1"])]
    assert client.get(f"{API}/providers/{world['p2']}", headers=h).status_code == 403
    assert client.patch(f"{API}/providers/{world['p2']}", json={"name": "Hacked name"}, headers=h).status_code == 403
    assert client.patch(f"{API}/providers/{world['p1']}", json={"name": "Renamed one"}, headers=h).status_code == 200
    assert client.get(f"{API}/providers/{world['p2']}/branches", headers=h).status_code == 403
    assert client.get(f"{API}/branches/{world['p2b1']}", headers=h).status_code == 403
    assert client.patch(f"{API}/branches/{world['p2b1']}", json={"name": "Hijack"}, headers=h).status_code == 403


def test_branch_admin_is_limited_to_own_branch(client, world, make_staff, login):
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    h = login(ba)
    assert client.get(f"{API}/branches/{world['p1b1']}", headers=h).status_code == 200
    assert client.get(f"{API}/branches/{world['p1b2']}", headers=h).status_code == 403   # sibling branch
    names = [b["name"] for b in client.get(f"{API}/providers/{world['p1']}/branches", headers=h).json()["items"]]
    assert names == ["p1-b1"]
    assert client.post(f"{API}/providers/{world['p1']}/branches", json={"name": "New one"}, headers=h).status_code == 403
    assert client.post(f"{API}/branches/{world['p1b1']}/deactivate", json={"reason": "closing it"}, headers=h).status_code == 403


def test_deactivate_requires_reason_and_is_audited(client, world, make_staff, login, db_factory):
    from institute_common.models.staff import AuditLog
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(sa)
    assert client.post(f"{API}/providers/{world['p1']}/deactivate", json={"reason": ""}, headers=h).status_code == 422
    assert client.post(f"{API}/providers/{world['p1']}/deactivate", json={"reason": "contract ended"}, headers=h).status_code == 200
    with db_factory() as db:
        row = db.query(AuditLog).filter_by(action="provider.deactivate").one()
        assert row.reason == "contract ended" and row.outcome == "SUCCESS"


def test_admin_cannot_touch_super_admin(client, world, make_staff, login):
    sa_id, _ = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, ad = make_staff([(Role.ADMIN, None, None)])
    h = login(ad)
    assert client.post(f"{API}/staff/{sa_id}/deactivate", json={"reason": "just because"}, headers=h).status_code == 403
    assert client.patch(f"{API}/staff/{sa_id}", json={"full_name": "Renamed SA"}, headers=h).status_code == 403
    body = {"role": "SUPER_ADMIN"}
    me_id = client.get(f"{API}/auth/me").json()["staff_id"]
    assert client.post(f"{API}/staff/{me_id}/assignments", json=body, headers=h).status_code == 403  # self-escalation
    other, _ = make_staff([])
    assert client.post(f"{API}/staff/{other}/assignments", json=body, headers=h).status_code == 403  # cannot mint a Super Admin


def test_role_grant_matrix(client, world, make_staff, login):
    other, _ = make_staff([(Role.TEACHER, world["p1"], None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    ok = client.post(f"{API}/staff/{other}/assignments", json={"role": "BRANCH_ADMIN", "provider_id": str(world["p1"]), "branch_id": str(world["p1b1"])}, headers=h)
    assert ok.status_code == 201
    denied = [
        {"role": "PROVIDER_ADMIN", "provider_id": str(world["p1"])},                  # peers
        {"role": "ADMIN"}, {"role": "SUPER_ADMIN"},
        {"role": "BRANCH_ADMIN", "provider_id": str(world["p2"]), "branch_id": str(world["p2b1"])},  # other tenant
        {"role": "TEACHER", "provider_id": str(world["p2"])},
    ]
    for body in denied:
        assert client.post(f"{API}/staff/{other}/assignments", json=body, headers=h).status_code == 403, body
    bad_scope = client.post(f"{API}/staff/{other}/assignments", json={"role": "BRANCH_ADMIN", "provider_id": str(world["p1"])}, headers=h)
    assert bad_scope.status_code == 403                                   # scope rule: BA needs a branch


def test_create_staff_sends_verification_and_user_sets_password(client, world, make_staff, login, outbox):
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    outbox.clear()
    r = client.post(f"{API}/staff", json={"full_name": "New Teacher", "email": "teach@example.com",
                    "assignment": {"role": "TEACHER", "provider_id": str(world["p1"])}}, headers=h)
    assert r.status_code == 201
    new_code = r.json()["staff_code"]
    assert re.fullmatch(r"STF\d{6}", new_code) and r.json()["email_verified"] is False
    assert len(outbox) == 1 and "30 minutes" in outbox[0].body
    token = re.search(r"token=([\w-]+)", outbox[0].body).group(1)
    # cannot log in before activation
    anon = client.post(f"{API}/auth/login", json={"staff_code": new_code, "password": PASSWORD}, headers=ORIGIN)
    assert anon.status_code == 401
    assert client.post(f"{API}/auth/verify-email", json={"token": token}, headers=ORIGIN).status_code == 422   # password required
    assert client.post(f"{API}/auth/verify-email", json={"token": token, "new_password": "short"}, headers=ORIGIN).status_code == 422
    assert client.post(f"{API}/auth/verify-email", json={"token": token, "new_password": "my long activation passphrase"}, headers=ORIGIN).status_code == 200
    assert client.post(f"{API}/auth/verify-email", json={"token": token, "new_password": "my long activation passphrase"}, headers=ORIGIN).status_code == 400


def test_one_global_identity_per_email_and_multi_provider_roles(client, world, make_staff, login):
    t_id, _ = make_staff([(Role.TEACHER, world["p1"], None)], email_addr="shared@example.com")
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    h = login(pa2)
    dup = client.post(f"{API}/staff", json={"full_name": "Shared Person", "email": "shared@example.com",
                      "assignment": {"role": "TEACHER", "provider_id": str(world["p2"])}}, headers=h)
    assert dup.status_code == 409
    found = client.get(f"{API}/staff/lookup", params={"email": "shared@example.com"}, headers=h).json()
    assert found[0]["staff_id"] == str(t_id)
    link = client.post(f"{API}/staff/{t_id}/assignments", json={"role": "TEACHER", "provider_id": str(world["p2"])}, headers=h)
    assert link.status_code == 201
    assert client.post(f"{API}/staff/{t_id}/assignments", json={"role": "TEACHER", "provider_id": str(world["p2"])}, headers=h).status_code == 409


def test_staff_directory_isolation(client, world, make_staff, login):
    t1, _ = make_staff([(Role.TEACHER, world["p1"], None)])
    t2, _ = make_staff([(Role.TEACHER, world["p2"], None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(pa)
    ids = {s["staff_id"] for s in client.get(f"{API}/staff", headers=h).json()["items"]}
    assert str(t1) in ids and str(t2) not in ids
    assert client.get(f"{API}/staff/{t2}", headers=h).status_code == 404       # existence not revealed
    assert client.patch(f"{API}/staff/{t2}", json={"full_name": "Someone Else"}, headers=h).status_code == 404
    assert client.post(f"{API}/staff/{t2}/deactivate", json={"reason": "no right"}, headers=h).status_code == 403


def test_teacher_has_no_admin_surface(client, world, make_staff, login):
    t, _ = make_staff([(Role.TEACHER, world["p1"], None)])
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    h = login(code)
    assert client.get(f"{API}/staff", headers=h).status_code == 403
    assert client.post(f"{API}/staff", json={"full_name": "Sneaky Add", "email": "s@example.com",
                       "assignment": {"role": "TEACHER", "provider_id": str(world["p1"])}}, headers=h).status_code == 403
    assert client.post(f"{API}/staff/{t}/assignments", json={"role": "TEACHER", "provider_id": str(world["p1"])}, headers=h).status_code == 403


def test_deactivation_kills_target_session_and_reactivation_restores(client, world, make_staff, login):
    from fastapi.testclient import TestClient
    from tests.gateway import app
    t, tcode = make_staff([(Role.TEACHER, world["p1"], None)])
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    teacher_http = TestClient(app, base_url="http://localhost")
    login(tcode, http=teacher_http)
    assert teacher_http.get(f"{API}/auth/me").status_code == 200
    h = login(sa)
    assert client.post(f"{API}/staff/{t}/deactivate", json={"reason": "left the company"}, headers=h).status_code == 200
    assert teacher_http.get(f"{API}/auth/me").status_code == 401
    assert teacher_http.post(f"{API}/auth/login", json={"staff_code": tcode, "password": PASSWORD}, headers=ORIGIN).status_code == 401
    assert client.post(f"{API}/staff/{t}/reactivate", json={"reason": "returned to work"}, headers=h).status_code == 200
    login(tcode, http=teacher_http)


def test_role_change_forces_reauthentication(client, world, make_staff, login):
    from fastapi.testclient import TestClient
    from tests.gateway import app
    t, tcode = make_staff([(Role.TEACHER, world["p1"], None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    th = TestClient(app, base_url="http://localhost")
    login(tcode, http=th)
    h = login(pa)
    a = client.get(f"{API}/staff/{t}", headers=h).json()["assignments"][0]["assignment_id"]
    assert client.request("DELETE", f"{API}/staff/{t}/assignments/{a}", json={"reason": "no longer teaching"}, headers=h).status_code == 200
    assert th.get(f"{API}/auth/me").status_code == 401


def test_pagination_bounds(client, world, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(sa)
    r = client.get(f"{API}/providers", params={"page_size": 1000}, headers=h).json()
    assert r["page_size"] == 100
    assert client.get(f"{API}/providers", params={"q": "%"}, headers=h).json()["total"] == 0   # LIKE wildcards escaped
