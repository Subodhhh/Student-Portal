
from institute_common.core.roles import Role

API = "/api/v1"


def idem(h, key):
    return {**h, "Idempotency-Key": key}


# ------------------------------------------------------------------ billing

def test_billing_flow_and_permission_split(client, world, make_staff, login):
    _, ad = make_staff([(Role.ADMIN, None, None)])
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(ad)
    inv = client.post(f"{API}/providers/{world['p1']}/invoices", json={"amount": "1000.00", "due_on": "2027-01-01"}, headers=h)
    assert inv.status_code == 201 and inv.json()["invoice_number"].startswith("INV-")
    iid = inv.json()["invoice_id"]
    # routine billing: record payments (idempotent), never over-collect
    assert client.post(f"{API}/invoices/{iid}/payments", json={"amount": "400.00", "method": "BANK_TRANSFER", "external_reference": "UTR12345"}, headers=h).status_code == 422  # key required
    pay = {"amount": "400.00", "method": "BANK_TRANSFER", "external_reference": "UTR12345"}
    r1 = client.post(f"{API}/invoices/{iid}/payments", json=pay, headers=idem(h, "pay-key-0001"))
    r2 = client.post(f"{API}/invoices/{iid}/payments", json=pay, headers=idem(h, "pay-key-0001"))
    assert r1.status_code == r2.status_code == 201 and r2.json()["paid"] == "400.00" and r2.json()["status"] == "PARTIALLY_PAID"
    assert client.post(f"{API}/invoices/{iid}/payments", json={**pay, "amount": "999.00"}, headers=idem(h, "pay-key-0001")).status_code == 409   # same key, different body
    assert client.post(f"{API}/invoices/{iid}/payments", json={**pay, "amount": "700.00"}, headers=idem(h, "pay-key-0002")).status_code == 409   # exceeds balance
    assert client.post(f"{API}/invoices/{iid}/payments", json={**pay, "amount": "600.00"}, headers=idem(h, "pay-key-0003")).json()["status"] == "PAID"
    # Admin must NOT refund or adjust
    adj = {"kind": "REFUND", "amount": "100.00", "reason": "duplicate payment"}
    assert client.post(f"{API}/invoices/{iid}/adjustments", json=adj, headers=idem(h, "adj-key-0001")).status_code == 403
    assert client.post(f"{API}/invoices/{iid}/adjustments", json={**adj, "kind": "WRITE_OFF"}, headers=idem(h, "adj-key-0002")).status_code == 403
    # Super Admin: reason mandatory, bounded by what was paid, idempotent
    hs = login(sa)
    assert client.post(f"{API}/invoices/{iid}/adjustments", json={**adj, "reason": ""}, headers=idem(hs, "adj-key-0003")).status_code == 422
    assert client.post(f"{API}/invoices/{iid}/adjustments", json={**adj, "amount": "5000.00"}, headers=idem(hs, "adj-key-0004")).status_code == 409
    ok = client.post(f"{API}/invoices/{iid}/adjustments", json=adj, headers=idem(hs, "adj-key-0005"))
    again = client.post(f"{API}/invoices/{iid}/adjustments", json=adj, headers=idem(hs, "adj-key-0005"))
    assert ok.status_code == again.status_code == 201 and again.json()["refunded"] == "100.00" and again.json()["net_paid"] == "900.00"
    assert again.json()["status"] == "PARTIALLY_PAID"
    detail = client.get(f"{API}/invoices/{iid}", headers=hs).json()
    assert detail["adjustments"][0]["reason"] == "duplicate payment" and len(detail["payments"]) == 2
    # Provider admin sees only own provider's billing, read-only
    hp = login(pa)
    assert client.get(f"{API}/providers/{world['p1']}/invoices", headers=hp).status_code == 200
    assert client.get(f"{API}/invoices/{iid}", headers=hp).status_code == 200
    assert client.post(f"{API}/invoices/{iid}/payments", json=pay, headers=idem(hp, "pay-key-0009")).status_code == 403
    assert client.post(f"{API}/providers/{world['p1']}/invoices", json={"amount": "5.00", "due_on": "2027-01-01"}, headers=hp).status_code == 403


def test_billing_tenant_isolation_and_audit(client, world, make_staff, login, db_factory):
    from institute_common.models.staff import AuditLog
    _, ad = make_staff([(Role.ADMIN, None, None)])
    iid = client.post(f"{API}/providers/{world['p1']}/invoices", json={"amount": "50.00", "due_on": "2027-01-01"}, headers=login(ad)).json()["invoice_id"]
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    h = login(pa2)
    assert client.get(f"{API}/invoices/{iid}", headers=h).status_code == 404
    assert client.get(f"{API}/providers/{world['p1']}/invoices", headers=h).status_code == 403
    assert client.get(f"{API}/providers/{world['p1']}/subscription", headers=h).status_code == 403
    with db_factory() as db:
        assert db.query(AuditLog).filter_by(action="billing.invoice_create").count() == 1


def test_plans_and_subscription(client, world, make_staff, login):
    _, ad = make_staff([(Role.ADMIN, None, None)])
    h = login(ad)
    plan = client.post(f"{API}/billing/plans", json={"code": "STARTER", "name": "Starter", "price": "999.00"}, headers=h)
    assert plan.status_code == 201
    assert client.post(f"{API}/billing/plans", json={"code": "STARTER", "name": "Dup", "price": "1.00"}, headers=h).status_code == 409
    assert client.post(f"{API}/billing/plans", json={"code": "NEG", "name": "Neg", "price": "-5"}, headers=h).status_code == 422
    r = client.post(f"{API}/providers/{world['p1']}/subscription", json={"plan_id": plan.json()["plan_id"], "starts_on": "2026-11-01"}, headers=h)
    assert r.status_code == 201
    assert client.get(f"{API}/providers/{world['p1']}/subscription", headers=h).json()["subscription"]["code"] == "STARTER"


# ------------------------------------------------------------------ support

def test_support_ticket_visibility(client, world, make_staff, login):
    t, tcode = make_staff([(Role.TEACHER, world["p1"], None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, pa2 = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    _, other_t = make_staff([(Role.TEACHER, world["p1"], None)])
    h = login(tcode)
    tk = client.post(f"{API}/support/tickets", json={"subject": "Cannot upload", "body": "CSV fails", "provider_id": str(world["p1"]),
                                                      "linked_ticket_ref": "student-portal:TCK-1"}, headers=h)
    assert tk.status_code == 201
    tid = tk.json()["ticket_id"]
    assert client.post(f"{API}/support/tickets", json={"subject": "Spoof", "body": "x", "provider_id": str(world["p2"])}, headers=h).status_code == 403
    assert client.get(f"{API}/support/tickets/{tid}", headers=h).status_code == 200
    assert client.patch(f"{API}/support/tickets/{tid}", json={"status": "RESOLVED"}, headers=h).status_code == 403           # creator may only close
    assert client.patch(f"{API}/support/tickets/{tid}", json={"status": "CLOSED"}, headers=h).status_code == 200
    hp = login(pa)
    assert [x["ticket_id"] for x in client.get(f"{API}/support/tickets", headers=hp).json()["items"]] == [tid]
    assert client.get(f"{API}/support/tickets/{tid}", headers=hp).status_code == 200
    assert client.get(f"{API}/support/tickets/{tid}", headers=login(pa2)).status_code == 404
    assert client.get(f"{API}/support/tickets/{tid}", headers=login(other_t)).status_code == 404
    assert client.get(f"{API}/support/tickets", headers=login(pa2)).json()["total"] == 0
    assert client.post(f"{API}/support/tickets/{tid}/messages", json={"body": "late reply"}, headers=login(pa)).status_code == 409   # closed


# ------------------------------------------------------------------ notifications

def test_notification_inbox_is_private(client, world, make_staff, login, db_factory):
    from institute_common.models.exams import Notification
    a, acode = make_staff([(Role.TEACHER, world["p1"], None)])
    b, bcode = make_staff([(Role.TEACHER, world["p1"], None)])
    with db_factory() as db:
        n = Notification(recipient_staff_id=a, kind="X", title="For A", body="b", dedupe_key="k1")
        db.add_all([n, Notification(recipient_staff_id=b, kind="X", title="For B", body="b", dedupe_key="k2")])
        db.commit()
        nid = str(n.notification_id)
    h = login(acode)
    assert client.get(f"{API}/notifications/unread-count", headers=h).json() == {"unread_count": 1}
    assert [x["title"] for x in client.get(f"{API}/notifications", headers=h).json()["items"]] == ["For A"]
    hb = login(bcode)
    assert client.post(f"{API}/notifications/{nid}/read", headers=hb).status_code == 404       # someone else's
    h = login(acode)
    assert client.post(f"{API}/notifications/{nid}/read", headers=h).json()["read_at"] is not None
    assert client.get(f"{API}/notifications/unread-count", headers=h).json() == {"unread_count": 0}


# ------------------------------------------------------------------ settings

def test_settings_scoping_and_validation(client, world, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, ad = make_staff([(Role.ADMIN, None, None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    body = {"key": "exam.default_duration_minutes", "value": 180}
    assert client.put(f"{API}/settings/platform", json=body, headers=login(ad)).status_code == 403          # Admin: no global settings
    assert client.put(f"{API}/settings/platform", json=body, headers=login(sa)).status_code == 200
    h = login(pa)
    assert client.put(f"{API}/settings/providers/{world['p1']}", json=body, headers=h).status_code == 200
    assert client.put(f"{API}/settings/providers/{world['p2']}", json=body, headers=h).status_code == 403
    assert client.put(f"{API}/settings/platform", json=body, headers=h).status_code == 403
    assert client.put(f"{API}/settings/providers/{world['p1']}", json={"key": "unknown.key", "value": 1}, headers=h).status_code == 422
    assert client.put(f"{API}/settings/providers/{world['p1']}", json={"key": "exam.default_duration_minutes", "value": 9999}, headers=h).status_code == 422
    assert client.put(f"{API}/settings/providers/{world['p1']}", json={"key": "exam.default_duration_minutes", "value": True}, headers=h).status_code == 422
    hb = login(ba)
    assert client.put(f"{API}/settings/branches/{world['p1b1']}", json={"key": "branding.display_name", "value": "Main Branch"}, headers=hb).status_code == 200
    assert client.put(f"{API}/settings/branches/{world['p1b2']}", json={"key": "branding.display_name", "value": "Nope"}, headers=hb).status_code == 403
    assert client.get(f"{API}/settings/providers/{world['p1']}", headers=hb).status_code == 403


# ------------------------------------------------------------------ audit view

def test_audit_view_is_scoped_and_restricted(client, world, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, pa1 = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    hs = login(sa)
    client.post(f"{API}/providers/{world['p1']}/deactivate", json={"reason": "audit demo p1"}, headers=hs)
    client.post(f"{API}/providers/{world['p2']}/deactivate", json={"reason": "audit demo p2"}, headers=hs)
    allrows = client.get(f"{API}/audit-logs", params={"action": "provider.deactivate"}, headers=hs).json()
    assert allrows["total"] == 2
    h1 = login(pa1)
    mine = client.get(f"{API}/audit-logs", headers=h1).json()
    assert mine["total"] >= 1 and all(r["provider_id"] == str(world["p1"]) for r in mine["items"])
    assert client.get(f"{API}/audit-logs", headers=login(ba)).status_code == 403
    assert "password" not in str(allrows).lower()


# ------------------------------------------------------------------ dashboards

def test_dashboards_return_scoped_real_numbers(client, world, make_staff, login, db_factory):
    from institute_common.models.org import Enrollment
    with db_factory() as db:
        for i, (prov, br) in enumerate([("p1", "p1b1"), ("p1", "p1b2"), ("p2", "p2b1")]):
            db.add(Enrollment(enrollment_id=f"e{i}", provider_id=world[prov], branch_id=world[br], student_id=f"STU00000{i}"))
        db.commit()
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    _, pa = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, t = make_staff([(Role.TEACHER, world["p1"], None)])
    d = client.get(f"{API}/dashboard", headers=login(sa)).json()
    assert d["role"] == "SUPER_ADMIN" and d["providers"] == 2 and d["branches"] == 4 and "security" in d and "operations" in d
    d = client.get(f"{API}/dashboard", headers=login(pa)).json()
    assert d["role"] == "PROVIDER_ADMIN" and d["branches"] == 2 and d["students"] == 2 and "security" not in d
    d = client.get(f"{API}/dashboard", headers=login(ba)).json()
    assert d["role"] == "BRANCH_ADMIN" and d["students"] == 1
    d = client.get(f"{API}/dashboard", headers=login(t)).json()
    assert d["role"] == "TEACHER" and d["teaching"] == [] and "providers" not in d and "students" not in d
