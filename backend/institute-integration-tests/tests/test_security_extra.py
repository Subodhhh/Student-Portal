import logging
import re


from institute_common.core.roles import ROLE_PERMISSIONS, Role
from tests.conftest import ORIGIN, PASSWORD

API = "/api/v1"


# ------------------------------------------------------------------ role / permission matrix (spec section 5)

SUPER_ONLY = {"paper.override_status", "result.correct", "billing.refund", "billing.adjust", "settings.platform"}


def test_only_super_admin_holds_exceptional_powers():
    assert SUPER_ONLY <= ROLE_PERMISSIONS[Role.SUPER_ADMIN]
    for role in (Role.ADMIN, Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN, Role.TEACHER):
        assert not (SUPER_ONLY & ROLE_PERMISSIONS[role]), role


def test_admin_has_routine_billing_but_no_refunds():
    assert "billing.routine" in ROLE_PERMISSIONS[Role.ADMIN] and "billing.refund" not in ROLE_PERMISSIONS[Role.ADMIN]


def test_teacher_cannot_administer():
    forbidden = {"staff.create", "staff.assign_role", "enrollment.manage", "enrollment.transfer_request", "enrollment.transfer_approve",
                 "billing.read", "billing.routine", "billing.refund", "settings.platform", "settings.provider", "result.correct", "examplan.manage",
                 "exam.generate", "provider.create", "batch.manage"}
    assert not (forbidden & ROLE_PERMISSIONS[Role.TEACHER])
    assert {"paper.approve", "paper.publish", "question.manage"} <= ROLE_PERMISSIONS[Role.TEACHER]   # may publish own papers


def test_branch_admin_cannot_approve_transfers_or_touch_billing():
    perms = ROLE_PERMISSIONS[Role.BRANCH_ADMIN]
    assert "enrollment.transfer_approve" not in perms and "enrollment.transfer_request" in perms
    assert not any(p.startswith("billing.") for p in perms) and "settings.platform" not in perms


def test_provider_admin_has_provider_scope_powers_but_no_platform_ones():
    perms = ROLE_PERMISSIONS[Role.PROVIDER_ADMIN]
    assert {"enrollment.transfer_approve", "examplan.manage", "billing.read"} <= perms
    assert "provider.create" not in perms and "settings.platform" not in perms and "billing.routine" not in perms


# ------------------------------------------------------------------ CORS / headers / errors

def test_cors_allows_only_listed_origin_and_never_wildcard(client):
    ok = client.options(f"{API}/auth/login", headers={"Origin": "http://localhost:5174", "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "content-type,x-csrf-token"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert ok.headers["access-control-allow-credentials"] == "true"
    bad = client.options(f"{API}/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
    assert "*" not in ok.headers.get("access-control-allow-origin", "")


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "no-store" in r.headers["cache-control"] and "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_unhandled_errors_do_not_leak_internals(client, monkeypatch):
    from inst_identity.routes import auth as auth_routes

    def boom(*a, **k):
        raise RuntimeError("SELECT * FROM staff WHERE password=secret-db-detail")

    monkeypatch.setattr(auth_routes, "verify_password", boom)
    from fastapi.testclient import TestClient
    from tests.gateway import app
    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as c:
        r = c.post(f"{API}/auth/login", json={"staff_code": "STF000001", "password": "x"}, headers=ORIGIN)
    assert r.status_code == 500 and r.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert "secret-db-detail" not in r.text and "Traceback" not in r.text


def test_search_input_is_parameterized(client, world, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(sa)
    for payload in ("' OR 1=1 --", "'; DROP TABLE providers; --", "%", "_", "\\"):
        r = client.get(f"{API}/providers", params={"q": payload}, headers=h)
        assert r.status_code == 200 and r.json()["total"] == 0
    assert client.get(f"{API}/providers", headers=h).json()["total"] == 2              # table intact


# ------------------------------------------------------------------ throttles and log hygiene

def test_forgot_password_is_throttled_without_revealing_it(client, world, make_staff, outbox):
    make_staff([(Role.TEACHER, world["p1"], None)], email_addr="t@example.com")
    bodies = []
    for _ in range(6):
        r = client.post(f"{API}/auth/forgot-password", json={"identifier": "t@example.com"}, headers=ORIGIN)
        assert r.status_code == 200
        bodies.append(r.json())
    assert len({str(b) for b in bodies}) == 1            # identical responses throttled or not
    assert len(outbox) == 3                              # but only 3 emails were actually sent


def test_mfa_verify_is_rate_limited_per_challenge(client, make_staff, outbox):
    _, code = make_staff([(Role.ADMIN, None, None)])
    cid = client.post(f"{API}/auth/login", json={"staff_code": code, "password": PASSWORD}, headers=ORIGIN).json()["challenge_id"]
    codes = [client.post(f"{API}/auth/mfa/verify", json={"challenge_id": cid, "otp": "000001"}, headers=ORIGIN).status_code for _ in range(12)]
    assert 429 in codes and codes[0] == 401


def test_mfa_issuance_is_rate_limited_per_account(client, make_staff, outbox):
    _, code = make_staff([(Role.ADMIN, None, None)])
    statuses = []
    for _ in range(7):
        r = client.post(f"{API}/auth/login", json={"staff_code": code, "password": PASSWORD}, headers=ORIGIN)
        statuses.append(r.status_code)
    assert 429 in statuses and len(outbox) <= 5


def test_secrets_never_reach_logs_or_audit(client, world, make_staff, outbox, caplog, db_factory):
    from institute_common.models.staff import AuditLog
    caplog.set_level(logging.DEBUG)
    make_staff([(Role.ADMIN, None, None)], email_addr="admin@example.com")
    r = client.post(f"{API}/auth/login", json={"staff_code": "STF000001", "password": PASSWORD}, headers=ORIGIN)
    otp = re.search(r"code is (\d{6})", outbox[-1].body).group(1)
    cid = r.json()["challenge_id"]
    ok = client.post(f"{API}/auth/mfa/verify", json={"challenge_id": cid, "otp": otp}, headers=ORIGIN)
    client.post(f"{API}/auth/forgot-password", json={"identifier": "admin@example.com"}, headers=ORIGIN)
    token = re.search(r"token=([\w-]+)", outbox[-1].body).group(1)
    sid = client.cookies.get("inst_sid")
    secrets_ = [otp, token, PASSWORD, cid, sid, ok.json()["me"]["csrf_token"]]
    haystack = caplog.text
    with db_factory() as db:
        haystack += " ".join(f"{a.action} {a.details} {a.reason} {a.target_id}" for a in db.query(AuditLog))
    for s in secrets_:
        assert s and s not in haystack


def test_upload_filename_cannot_escape_the_store(client, world, make_staff, login, tmp_path, db_factory):
    import io
    from institute_common.models.academics import ProviderSubject, Subject
    from inst_paper.services.filestore import LocalFileStore, set_store
    set_store(LocalFileStore(str(tmp_path / "files")))
    try:
        _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
        h = login(ba)
        with db_factory() as db:
            subj = Subject(code="PHY", name="Physics")
            db.add(subj)
            db.flush()
            db.add(ProviderSubject(provider_id=world["p1"], subject_id=subj.subject_id))
            db.commit()
            subject_id = subj.subject_id
        csv = "question,option_a,option_b,option_c,option_d,correct,marks,negative_marks\nQ?,a,b,c,d,A,4,1\n"
        r = client.post(f"{API}/branches/{world['p1b1']}/subjects/{subject_id}/papers/upload",
                        files={"file": ("../../../etc/passwd.csv", io.BytesIO(csv.encode()), "text/csv")}, data={"title": "Traversal"}, headers=h)
        assert r.status_code == 201
        stored = list((tmp_path / "files").iterdir())
        assert len(stored) == 1 and re.fullmatch(r"[0-9a-f]{32}", stored[0].name)      # server-generated key only
        assert not (tmp_path / "etc").exists()
    finally:
        set_store(None)


# ------------------------------------------------------------------ audit of cross-tenant probes

def test_cross_tenant_probes_leave_denied_audit_records(client, world, make_staff, login, db_factory):
    """Every way of poking another tenant's objects (403 *or* the 404 used to hide existence) is audited
    with the actor, so suspected cross-tenant access is visible."""
    from institute_common.models.ops import SupportTicket
    from institute_common.models.staff import AuditLog
    victim_id, _ = make_staff([(Role.TEACHER, world["p1"], None)])
    with db_factory() as db:
        t = SupportTicket(provider_id=world["p1"], created_by=victim_id, subject="Private ticket")
        db.add(t)
        db.commit()
        ticket_id = t.ticket_id
    attacker_id, code = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    h = login(code)
    probes = [
        client.get(f"{API}/staff/{victim_id}", headers=h),                       # hidden (404)
        client.patch(f"{API}/staff/{victim_id}", json={"full_name": "Pwned Name"}, headers=h),
        client.get(f"{API}/support/tickets/{ticket_id}", headers=h),             # hidden (404)
        client.get(f"{API}/providers/{world['p1']}/branches", headers=h),        # 403
        client.get(f"{API}/branches/{world['p1b1']}", headers=h),                # 403
    ]
    assert [r.status_code for r in probes] == [404, 404, 404, 403, 403]
    with db_factory() as db:
        rows = db.query(AuditLog).filter(AuditLog.outcome == "DENIED", AuditLog.actor_staff_id == attacker_id).all()
        actions = {r.action for r in rows}
        assert {"authz.out_of_scope", "authz.denied"} <= actions and len(rows) >= 5
