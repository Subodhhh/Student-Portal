import re

import pytest

from app.core import redis_store, sessions
from app.core.roles import Role
from tests.conftest import ORIGIN, PASSWORD


def post(client, path, body=None, headers=ORIGIN):
    return client.post(path, json=body or {}, headers=headers)


def test_teacher_logs_in_without_mfa(client, world, make_staff, login):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    h = login(code)
    me = client.get("/api/v1/auth/me").json()
    assert me["primary_role"] == "TEACHER" and me["staff_code"] == code
    assert "billing.refund" not in me["permissions"]
    assert h["X-CSRF-Token"] == me["csrf_token"]


@pytest.mark.parametrize("role,scope", [
    (Role.SUPER_ADMIN, (None, None)), (Role.ADMIN, (None, None)),
    (Role.PROVIDER_ADMIN, ("p1", None)), (Role.BRANCH_ADMIN, ("p1", "p1b1")),
])
def test_privileged_roles_require_mfa(client, world, make_staff, outbox, role, scope):
    _, code = make_staff([(role, world.get(scope[0]), world.get(scope[1]))])
    r = post(client, "/api/v1/auth/login", {"staff_code": code, "password": PASSWORD})
    assert r.json()["status"] == "MFA_REQUIRED"
    assert "set-cookie" not in r.headers          # no session before OTP
    assert client.get("/api/v1/auth/me").status_code == 401
    assert len(outbox) == 1


def test_mfa_wrong_otp_limit_and_reuse(client, world, make_staff, outbox):
    _, code = make_staff([(Role.ADMIN, None, None)])
    cid = post(client, "/api/v1/auth/login", {"staff_code": code, "password": PASSWORD}).json()["challenge_id"]
    real = re.search(r"code is (\d{6})", outbox[-1].body).group(1)
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert post(client, "/api/v1/auth/mfa/verify", {"challenge_id": cid, "otp": wrong}).status_code == 401
    # challenge is burned after 5 failures: even the right code fails
    assert post(client, "/api/v1/auth/mfa/verify", {"challenge_id": cid, "otp": real}).status_code == 401


def test_otp_single_use_and_replacement_invalidates_previous(client, make_staff, outbox):
    _, code = make_staff([(Role.ADMIN, None, None)])
    cid = post(client, "/api/v1/auth/login", {"staff_code": code, "password": PASSWORD}).json()["challenge_id"]
    first = re.search(r"code is (\d{6})", outbox[-1].body).group(1)
    assert post(client, "/api/v1/auth/mfa/resend", {"challenge_id": cid}).status_code == 429  # cooldown
    # age the challenge past the cooldown, then resend
    import json
    r = redis_store.client()
    d = json.loads(r.get(f"inst_mfa:{cid}"))
    d["issued_at"] = "2000-01-01T00:00:00+00:00"
    r.set(f"inst_mfa:{cid}", json.dumps(d), ex=300)
    assert post(client, "/api/v1/auth/mfa/resend", {"challenge_id": cid}).status_code == 200
    second = re.search(r"code is (\d{6})", outbox[-1].body).group(1)
    if first != second:
        assert post(client, "/api/v1/auth/mfa/verify", {"challenge_id": cid, "otp": first}).status_code == 401
    assert post(client, "/api/v1/auth/mfa/verify", {"challenge_id": cid, "otp": second}).status_code == 200
    assert post(client, "/api/v1/auth/mfa/verify", {"challenge_id": cid, "otp": second}).status_code == 401


def test_unverified_email_blocks_privileged_login(client, make_staff):
    _, code = make_staff([(Role.ADMIN, None, None)], verified=False)
    r = post(client, "/api/v1/auth/login", {"staff_code": code, "password": PASSWORD})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"


def test_generic_failures_and_inactive(client, world, make_staff):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    _, inactive = make_staff([(Role.TEACHER, world["p1"], None)], active=False)
    _, no_role = make_staff([])
    results = [
        post(client, "/api/v1/auth/login", {"staff_code": c, "password": p})
        for c, p in [(code, "wrong password here"), ("STF999999", PASSWORD), (inactive, PASSWORD), (no_role, PASSWORD)]
    ]
    assert {r.status_code for r in results} == {401}
    assert len({str(r.json()) for r in results}) == 1


def test_login_rate_limit_per_account(client, world, make_staff):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    codes = [post(client, "/api/v1/auth/login", {"staff_code": code, "password": "wrong password!!"}).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


def test_second_login_replaces_first_session(client, world, make_staff, login):
    from fastapi.testclient import TestClient
    from app.main import app
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    login(code)
    assert client.get("/api/v1/auth/me").status_code == 200
    other = TestClient(app, base_url="http://localhost")
    login(code, http=other)
    assert other.get("/api/v1/auth/me").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401   # first session revoked


def test_logout_and_csrf(client, world, make_staff, login):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    h = login(code)
    assert client.post("/api/v1/auth/logout", headers=ORIGIN).status_code == 403          # no CSRF token
    assert client.post("/api/v1/auth/logout", headers={**ORIGIN, "X-CSRF-Token": "bad"}).status_code == 403
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_foreign_origin_rejected(client, world, make_staff):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    r = client.post("/api/v1/auth/login", json={"staff_code": code, "password": PASSWORD},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "ORIGIN_NOT_ALLOWED"


def test_cookie_attributes(client, world, make_staff):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    r = post(client, "/api/v1/auth/login", {"staff_code": code, "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "domain=" not in cookie
    assert "csrf" not in cookie   # cookie carries only the opaque session id


def test_student_session_not_accepted(client, world, make_staff):
    make_staff([(Role.TEACHER, world["p1"], None)])
    client.cookies.set("inst_sid", "x")
    client.cookies.set("session_id", "anything")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_expiry_idle_and_absolute(client, world, make_staff, login):
    import json
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    login(code)
    r = redis_store.client()
    key = next(k for k in r.keys("inst_session:*"))
    assert 0 < r.ttl(key) <= 1800                                   # 30-minute idle window
    d = json.loads(r.get(key))
    d["expires_at"] = "2000-01-01T00:00:00+00:00"                   # past the 12 h absolute cap
    r.set(key, json.dumps(d), ex=1800)
    assert client.get("/api/v1/auth/me").status_code == 401
    assert sessions.ABSOLUTE_LIFETIME.total_seconds() == 12 * 3600


def test_deactivated_staff_loses_access_immediately(client, world, make_staff, login, db_factory):
    from app.models.staff import Staff
    sid, code = make_staff([(Role.TEACHER, world["p1"], None)])
    login(code)
    with db_factory() as db:
        db.get(Staff, sid).status = "INACTIVE"
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_change_invalidates_sessions_and_enforces_policy(client, world, make_staff, login):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)])
    h = login(code)
    weak = client.post("/api/v1/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}, headers=h)
    assert weak.status_code == 422 and weak.json()["detail"]["code"] == "WEAK_PASSWORD"
    wrong = client.post("/api/v1/auth/change-password", json={"current_password": "nope nope nope", "new_password": "a brand new long passphrase"}, headers=h)
    assert wrong.status_code == 403
    ok = client.post("/api/v1/auth/change-password", json={"current_password": PASSWORD, "new_password": "a brand new long passphrase"}, headers=h)
    assert ok.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    login(code, password="a brand new long passphrase")


def test_forgot_and_reset_flow(client, world, make_staff, outbox, login):
    _, code = make_staff([(Role.TEACHER, world["p1"], None)], email_addr="t@example.com")
    _, unverified = make_staff([(Role.TEACHER, world["p1"], None)], verified=False, email_addr="u@example.com")
    r1 = post(client, "/api/v1/auth/forgot-password", {"identifier": "t@example.com"})
    r2 = post(client, "/api/v1/auth/forgot-password", {"identifier": "nobody@example.com"})
    r3 = post(client, "/api/v1/auth/forgot-password", {"identifier": unverified})
    assert r1.json() == r2.json() == r3.json()
    assert len(outbox) == 1                                  # only the verified account got mail
    token = re.search(r"token=([\w-]+)", outbox[0].body).group(1)
    assert "15 minutes" in outbox[0].body
    assert post(client, "/api/v1/auth/reset-password", {"token": token, "new_password": "short"}).status_code == 422
    assert post(client, "/api/v1/auth/reset-password", {"token": token, "new_password": "totally new passphrase"}).status_code == 200
    assert post(client, "/api/v1/auth/reset-password", {"token": token, "new_password": "totally new passphrase 2"}).status_code == 400  # single use
    login(code, password="totally new passphrase")


def test_reset_token_expiry(client, world, make_staff, outbox, db_factory):
    from datetime import timedelta
    from app.database import utcnow
    from app.models.staff import StaffToken
    make_staff([(Role.TEACHER, world["p1"], None)], email_addr="t@example.com")
    post(client, "/api/v1/auth/forgot-password", {"identifier": "t@example.com"})
    token = re.search(r"token=([\w-]+)", outbox[0].body).group(1)
    with db_factory() as db:
        for t in db.query(StaffToken).all():
            t.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert post(client, "/api/v1/auth/reset-password", {"token": token, "new_password": "totally new passphrase"}).status_code == 400


def test_password_policy_unit():
    from app.core.passwords import validate_password
    assert validate_password("short")
    assert validate_password("a" * 129)
    assert validate_password("passwordpassword")
    assert validate_password("zzzzzzzzzzzzzzzz")
    assert validate_password("my staff stf000001 pass", personal=["STF000001"])
    assert not validate_password("lowercase only but long enough")        # no composition rules
    assert not validate_password("x" * 20 + "yz1")
    assert not validate_password("p" * 100 + "q!" * 14)                   # 128 accepted
