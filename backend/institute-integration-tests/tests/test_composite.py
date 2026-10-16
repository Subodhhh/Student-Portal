"""Composite tests across providers: A starts a request, B and C contribute approved papers, A builds the exam (tests first)."""
import pytest

from institute_common.core.roles import Role
from tests.test_exams import API, ctx, make_paper, pa, plan_body  # noqa: F401  (fixtures)

KEY = "Idempotency-Key"


@pytest.fixture()
def third(db_factory, ctx):  # noqa: F811
    """Provider p3 with a branch and both subjects enabled."""
    from institute_common.models.academics import ProviderSubject
    from institute_common.models.org import Branch, Provider
    with db_factory() as db:
        p = Provider(name="Provider p3", email="p3@example.com")
        db.add(p)
        db.flush()
        b = Branch(provider_id=p.provider_id, name="p3-b1")
        db.add(b)
        db.flush()
        for s in (ctx["phy"], ctx["chem"]):
            db.add(ProviderSubject(provider_id=p.provider_id, subject_id=s))
        db.commit()
        return {"p": p.provider_id, "b": b.branch_id}


@pytest.fixture()
def actors(client, world, third, make_staff, login):
    """A (p1), B (p2) and C (p3) provider admins, plus a branch admin of B. One shared HTTP client holds a single session cookie, so each
    actor's session cookie travels explicitly in its headers and every actor can be used at any time."""
    def sign_in(code):
        h = login(code)
        return {**h, "Cookie": f"inst_sid={client.cookies.get('inst_sid')}"}
    out = {"A": sign_in(make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])[1]),
           "B": sign_in(make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])[1]),
           "C": sign_in(make_staff([(Role.PROVIDER_ADMIN, third["p"], None)])[1]),
           "B_branch": sign_in(make_staff([(Role.BRANCH_ADMIN, world["p2"], world["p2b1"])])[1])}
    client.cookies.clear()
    return out


def request_body(ctx, world, third, **over):  # noqa: F811
    body = {"name": "Joint NEET Mock", "note": "Physics from B, Chemistry from C",
            "slots": [{"subject_id": str(ctx["phy"]), "provider_id": str(world["p2"])},
                      {"subject_id": str(ctx["chem"]), "provider_id": str(third["p"])}]}
    body.update(over)
    return body


def start(client, h, world, ctx, third, key="req-key-0001", **over):  # noqa: F811
    return client.post(f"{API}/branches/{world['p1b1']}/composite-requests", json=request_body(ctx, world, third, **over), headers={**h, KEY: key})


def invitations(client, h):
    return client.get(f"{API}/composite-invitations", headers=h).json()


def notices(db_factory, kind):
    from institute_common.models.exams import Notification
    with db_factory() as db:
        return [(n.recipient_staff_id, n.provider_id) for n in db.query(Notification).filter_by(kind=kind)]


# ------------------------------------------------------------------ starting a request

def test_initiator_starts_a_request_and_invited_providers_are_notified_once(client, world, ctx, third, actors, db_factory):  # noqa: F811
    from institute_common.models.staff import AuditLog
    r = start(client, actors["A"], world, ctx, third)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "OPEN" and [s["status"] for s in body["slots"]] == ["PENDING", "PENDING"]
    assert sorted(m["provider_id"] for m in body["missing"]) == sorted([str(world["p2"]), str(third["p"])])
    got = notices(db_factory, "COMPOSITE_INVITATION")
    assert sorted(str(p) for _, p in got) == sorted([str(world["p2"]), str(third["p"])])       # one per invited provider's admin
    assert len(got) == 2
    with db_factory() as db:
        assert db.query(AuditLog).filter_by(action="composite.request", outcome="SUCCESS").count() == 1
    again = start(client, actors["A"], world, ctx, third)                                        # same Idempotency-Key => same request
    assert again.status_code == 200 and again.json()["request_id"] == body["request_id"]
    assert len(notices(db_factory, "COMPOSITE_INVITATION")) == 2                                 # nobody is notified twice


def test_duplicate_open_requests_are_refused_and_a_key_is_required(client, world, ctx, third, actors):  # noqa: F811
    assert start(client, actors["A"], world, ctx, third).status_code == 201
    dup = start(client, actors["A"], world, ctx, third, key="req-key-0002")
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "DUPLICATE_REQUEST"
    assert client.post(f"{API}/branches/{world['p1b1']}/composite-requests", json=request_body(ctx, world, third, name="Other name"),
                       headers=actors["A"]).status_code == 422
    assert start(client, actors["A"], world, ctx, third, key="req-key-0003", name="Another joint mock").status_code == 201


def test_a_provider_can_contribute_to_its_own_request_slots_too(client, world, ctx, third, actors):  # noqa: F811
    r = start(client, actors["A"], world, ctx, third, slots=[{"subject_id": str(ctx["phy"]), "provider_id": str(world["p1"])}])
    assert r.status_code == 201 and r.json()["slots"][0]["status"] == "PENDING"
    got = client.get(f"{API}/composite-invitations", headers=actors["A"]).json()
    assert [i["slot"]["subject_id"] for i in got] == [str(ctx["phy"])]                          # A is asked to supply it itself


@pytest.mark.parametrize("mutate,status", [
    (lambda b, c, w, t: b.update(slots=[]), 422),
    (lambda b, c, w, t: b.update(slots=[{"subject_id": str(c["phy"]), "provider_id": str(w["p2"])}] * 2), 422),                      # same subject twice
    (lambda b, c, w, t: b.update(slots=[{"subject_id": str(c["phy"]), "provider_id": "00000000-0000-0000-0000-000000000009"}]), 422),   # unknown provider
    (lambda b, c, w, t: b.update(slots=[{"subject_id": "00000000-0000-0000-0000-000000000009", "provider_id": str(w["p2"])}]), 422),   # unknown subject
    (lambda b, c, w, t: b.update(name="x"), 422),
])
def test_invalid_requests_are_rejected(client, world, ctx, third, actors, db_factory, mutate, status):  # noqa: F811
    from institute_common.models.exams import CompositeRequest
    body = request_body(ctx, world, third)
    mutate(body, ctx, world, third)
    r = client.post(f"{API}/branches/{world['p1b1']}/composite-requests", json=body, headers={**actors["A"], KEY: "req-key-9999"})
    assert r.status_code == status
    with db_factory() as db:
        assert db.query(CompositeRequest).count() == 0


def test_subject_must_be_enabled_for_initiator_and_contributor(client, world, ctx, third, actors, db_factory):  # noqa: F811
    from institute_common.models.academics import ProviderSubject
    with db_factory() as db:
        db.query(ProviderSubject).filter_by(provider_id=world["p2"], subject_id=ctx["phy"]).delete()
        db.commit()
    r = start(client, actors["A"], world, ctx, third)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "SUBJECT_NOT_ENABLED"


def test_only_plan_managers_of_the_initiating_provider_can_start_requests(client, world, ctx, third, actors, make_staff, login):  # noqa: F811
    assert start(client, actors["B"], world, ctx, third).status_code == 403                    # another provider's admin
    assert start(client, login(ctx["tphy"]), world, ctx, third).status_code == 403             # a teacher
    _, ba = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    assert start(client, login(ba), world, ctx, third).status_code == 403                      # branch admins do not manage plans
    client.cookies.clear()
    assert client.post(f"{API}/branches/{world['p1b1']}/composite-requests", json={}, headers={"Origin": "http://localhost:5174"}).status_code == 401


# ------------------------------------------------------------------ the invited provider's side

def test_invited_providers_see_only_their_own_invitations(client, world, ctx, third, actors):  # noqa: F811
    start(client, actors["A"], world, ctx, third)
    b = invitations(client, actors["B"])
    c = invitations(client, actors["C"])
    assert [i["slot"]["subject_id"] for i in b] == [str(ctx["phy"])] and [i["slot"]["subject_id"] for i in c] == [str(ctx["chem"])]
    assert b[0]["request"]["initiator_provider_id"] == str(world["p1"]) and b[0]["request"]["name"] == "Joint NEET Mock"
    assert "slots" not in b[0]["request"]                                                       # no other provider's slots are revealed
    assert all(i["slot"]["provider_id"] == str(world["p2"]) for i in b)
    uninvolved = client.get(f"{API}/composite-invitations", headers=actors["B_branch"]).json()
    assert uninvolved == []                                                                     # a branch admin is not the provider's decision maker


def test_request_details_visibility(client, world, ctx, third, actors):  # noqa: F811
    rid = start(client, actors["A"], world, ctx, third).json()["request_id"]
    assert len(client.get(f"{API}/composite-requests/{rid}", headers=actors["A"]).json()["slots"]) == 2
    mine = client.get(f"{API}/composite-requests/{rid}", headers=actors["B"]).json()
    assert [s["provider_id"] for s in mine["slots"]] == [str(world["p2"])]
    from tests.test_composite import third as _t  # noqa: F401
    assert client.get(f"{API}/composite-requests/00000000-0000-0000-0000-000000000009", headers=actors["A"]).status_code == 404


def publish(db_factory, provider, branch, subject, title, status="PUBLISHED"):
    return make_paper(db_factory, provider, branch, subject, title, status)


def test_contribution_validates_shares_the_paper_and_notifies_the_initiator(client, world, ctx, third, actors, db_factory):  # noqa: F811
    from institute_common.models.papers import PaperShare
    from institute_common.models.staff import AuditLog
    rid_body = start(client, actors["A"], world, ctx, third).json()
    rid, slot = rid_body["request_id"], rid_body["slots"][0]["slot_id"]
    paper, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    r = client.post(f"{API}/composite-requests/{rid}/slots/{slot}/contribute", json={"paper_version_id": str(pv)}, headers=actors["B"])
    assert r.status_code == 200, r.text
    assert r.json()["slot"]["status"] == "CONTRIBUTED" and r.json()["slot"]["paper_version_id"] == str(pv) and r.json()["status"] == "OPEN"
    with db_factory() as db:
        share = db.query(PaperShare).filter_by(paper_id=paper).one()
        assert (share.owner_provider_id, share.shared_with_provider_id, share.status) == (world["p2"], world["p1"], "ACTIVE")
        assert db.query(AuditLog).filter_by(action="composite.contribute", outcome="SUCCESS").count() == 1
    assert len(notices(db_factory, "COMPOSITE_CONTRIBUTION")) >= 1                              # the initiator's staff were told
    # the initiator may now USE the paper in a plan, but still cannot read its questions
    ok = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", json=plan_body(ctx, slots=[{"subject_id": str(ctx["phy"]), "paper_version_id": str(pv)}]),
                     headers=actors["A"])
    assert ok.status_code == 201, ok.text
    assert client.get(f"{API}/paper-versions/{pv}", headers=actors["A"]).status_code == 403
    assert [m["status"] for m in client.get(f"{API}/composite-requests/{rid}", headers=actors["A"]).json()["missing"]] == ["PENDING"]


def test_request_completes_when_every_slot_is_contributed(client, world, ctx, third, actors, db_factory):  # noqa: F811
    body = start(client, actors["A"], world, ctx, third).json()
    rid = body["request_id"]
    _, pv_b = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    _, pv_c = publish(db_factory, third["p"], third["b"], ctx["chem"], "C Chemistry")
    for s, who, pv in ((body["slots"][0], "B", pv_b), (body["slots"][1], "C", pv_c)):
        assert client.post(f"{API}/composite-requests/{rid}/slots/{s['slot_id']}/contribute", json={"paper_version_id": str(pv)}, headers=actors[who]).status_code == 200
    done = client.get(f"{API}/composite-requests/{rid}", headers=actors["A"]).json()
    assert done["status"] == "COMPLETE" and done["missing"] == []


def test_a_b_c_composite_exam_records_every_contributor_and_exact_version(client, world, ctx, third, actors, db_factory):  # noqa: F811
    body = start(client, actors["A"], world, ctx, third).json()
    rid = body["request_id"]
    _, pv_b = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    _, pv_c = publish(db_factory, third["p"], third["b"], ctx["chem"], "C Chemistry")
    for s, who, pv in ((body["slots"][0], "B", pv_b), (body["slots"][1], "C", pv_c)):
        client.post(f"{API}/composite-requests/{rid}/slots/{s['slot_id']}/contribute", json={"paper_version_id": str(pv)}, headers=actors[who])
    plan = client.post(f"{API}/branches/{world['p1b1']}/exam-plans", headers=actors["A"], json=plan_body(
        ctx, slots=[{"subject_id": str(ctx["phy"]), "paper_version_id": str(pv_b)}, {"subject_id": str(ctx["chem"]), "paper_version_id": str(pv_c)}])).json()
    job = client.post(f"{API}/exam-plans/{plan['plan_id']}/generate", json={}, headers=actors["A"])
    assert job.status_code == 200 and job.json()["status"] == "SUCCEEDED", job.text
    exam = client.get(f"{API}/exams/{job.json()['exam_id']}", headers=actors["A"]).json()
    assert {(p["source_provider_id"], p["paper_version_id"]) for p in exam["papers"]} == {(str(world["p2"]), str(pv_b)), (str(third["p"]), str(pv_c))}
    # revoking a share afterwards does not alter the already generated manifest
    paper_b = client.get(f"{API}/paper-versions/{pv_b}", headers=actors["B"]).json()["paper_id"]
    assert client.delete(f"{API}/papers/{paper_b}/shares/{world['p1']}", headers=actors["B"]).status_code == 204
    again = client.get(f"{API}/exams/{job.json()['exam_id']}", headers=actors["A"]).json()
    assert {p["paper_version_id"] for p in again["papers"]} == {str(pv_b), str(pv_c)}


@pytest.mark.parametrize("case", ["draft", "wrong_subject", "foreign_paper", "withdrawn"])
def test_invalid_contributions_are_refused_without_sharing_anything(client, world, ctx, third, actors, db_factory, case):  # noqa: F811
    from institute_common.models.papers import PaperShare, PaperVersion
    body = start(client, actors["A"], world, ctx, third).json()
    rid, slot = body["request_id"], body["slots"][0]["slot_id"]                                # Physics from B
    if case == "draft":
        _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B draft", "VALIDATED")
    elif case == "wrong_subject":
        _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["chem"], "B chem")
    elif case == "foreign_paper":
        _, pv = publish(db_factory, third["p"], third["b"], ctx["phy"], "C physics")
    else:
        _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B withdrawn", "WITHDRAWN")
    r = client.post(f"{API}/composite-requests/{rid}/slots/{slot}/contribute", json={"paper_version_id": str(pv)}, headers=actors["B"])
    assert r.status_code in (403, 409, 422), r.text
    with db_factory() as db:
        assert db.query(PaperShare).count() == 0 and db.query(PaperVersion).count() >= 1
    assert client.get(f"{API}/composite-requests/{rid}", headers=actors["A"]).json()["slots"][0]["status"] == "PENDING"


def test_only_the_invited_providers_decision_makers_may_contribute_or_decline(client, world, ctx, third, actors, make_staff, login, db_factory):  # noqa: F811
    body = start(client, actors["A"], world, ctx, third).json()
    rid, slot = body["request_id"], body["slots"][0]["slot_id"]
    _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    url = f"{API}/composite-requests/{rid}/slots/{slot}"
    assert client.post(f"{url}/contribute", json={"paper_version_id": str(pv)}, headers=actors["C"]).status_code in (403, 404)    # another provider
    assert client.post(f"{url}/contribute", json={"paper_version_id": str(pv)}, headers=actors["A"]).status_code in (403, 404)    # the initiator cannot answer for B
    teacher = login(make_staff([(Role.TEACHER, world["p2"], None)])[1])
    assert client.post(f"{url}/contribute", json={"paper_version_id": str(pv)}, headers=teacher).status_code == 403
    assert client.post(f"{url}/decline", json={"reason": "we cannot supply this"}, headers=actors["C"]).status_code in (403, 404)
    assert client.post(f"{url}/contribute", json={"paper_version_id": str(pv)}, headers=actors["B_branch"]).status_code == 200      # branch admin of the paper's branch may


def test_contributing_twice_is_refused_and_declining_closes_the_slot(client, world, ctx, third, actors, db_factory):  # noqa: F811
    body = start(client, actors["A"], world, ctx, third).json()
    rid = body["request_id"]
    s_b, s_c = body["slots"][0]["slot_id"], body["slots"][1]["slot_id"]
    _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    url = f"{API}/composite-requests/{rid}/slots/{s_b}/contribute"
    assert client.post(url, json={"paper_version_id": str(pv)}, headers=actors["B"]).status_code == 200
    assert client.post(url, json={"paper_version_id": str(pv)}, headers=actors["B"]).status_code == 409
    assert client.post(f"{API}/composite-requests/{rid}/slots/{s_c}/decline", json={"reason": ""}, headers=actors["C"]).status_code == 422
    d = client.post(f"{API}/composite-requests/{rid}/slots/{s_c}/decline", json={"reason": "No Chemistry paper is ready"}, headers=actors["C"])
    assert d.status_code == 200 and d.json()["slot"]["status"] == "DECLINED"
    assert client.post(f"{API}/composite-requests/{rid}/slots/{s_c}/contribute", json={"paper_version_id": str(pv)}, headers=actors["C"]).status_code == 409
    detail = client.get(f"{API}/composite-requests/{rid}", headers=actors["A"]).json()
    assert detail["status"] == "OPEN" and [m["status"] for m in detail["missing"]] == ["DECLINED"]                # A may still generate without Chemistry
    assert len(notices_for(client, actors["A"], "COMPOSITE_DECLINED")) == 1


def notices_for(client, h, kind):
    return [n for n in client.get(f"{API}/notifications", headers=h).json()["items"] if n["kind"] == kind]


def test_cancelling_blocks_further_contributions(client, world, ctx, third, actors, db_factory):  # noqa: F811
    body = start(client, actors["A"], world, ctx, third).json()
    rid, slot = body["request_id"], body["slots"][0]["slot_id"]
    _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    assert client.post(f"{API}/composite-requests/{rid}/cancel", json={"reason": "exam postponed"}, headers=actors["B"]).status_code in (403, 404)
    assert client.post(f"{API}/composite-requests/{rid}/cancel", json={"reason": "x"}, headers=actors["A"]).status_code == 422
    c = client.post(f"{API}/composite-requests/{rid}/cancel", json={"reason": "exam postponed"}, headers=actors["A"])
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    assert client.post(f"{API}/composite-requests/{rid}/slots/{slot}/contribute", json={"paper_version_id": str(pv)}, headers=actors["B"]).status_code == 409
    assert client.post(f"{API}/composite-requests/{rid}/cancel", json={"reason": "again please"}, headers=actors["A"]).status_code == 409
    # the name is free again after cancelling
    assert start(client, actors["A"], world, ctx, third, key="req-key-5555").status_code == 201


def test_a_failed_share_leaves_the_slot_pending_and_can_be_retried(client, world, ctx, third, actors, db_factory, monkeypatch):  # noqa: F811
    from institute_common import internal_http
    from institute_common.models.papers import PaperShare
    body = start(client, actors["A"], world, ctx, third).json()
    rid, slot = body["request_id"], body["slots"][0]["slot_id"]
    _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    real = internal_http._PEERS.pop("paper")
    try:
        r = client.post(f"{API}/composite-requests/{rid}/slots/{slot}/contribute", json={"paper_version_id": str(pv)}, headers=actors["B"])
    finally:
        internal_http._PEERS["paper"] = real
    assert r.status_code == 503 and r.json()["detail"]["code"] == "PAPER_SERVICE_UNAVAILABLE"
    assert client.get(f"{API}/composite-requests/{rid}", headers=actors["A"]).json()["slots"][0]["status"] == "PENDING"
    with db_factory() as db:
        assert db.query(PaperShare).count() == 0
    assert client.post(f"{API}/composite-requests/{rid}/slots/{slot}/contribute", json={"paper_version_id": str(pv)}, headers=actors["B"]).status_code == 200


def test_listing_requests_is_scoped_to_the_initiating_branch(client, world, ctx, third, actors):  # noqa: F811
    start(client, actors["A"], world, ctx, third)
    page = client.get(f"{API}/branches/{world['p1b1']}/composite-requests", headers=actors["A"]).json()
    assert page["total"] == 1 and page["items"][0]["name"] == "Joint NEET Mock"
    assert client.get(f"{API}/branches/{world['p1b1']}/composite-requests", headers=actors["B"]).status_code == 403
    assert client.get(f"{API}/branches/{world['p1b1']}/composite-requests?status=CANCELLED", headers=actors["A"]).json()["total"] == 0


def test_provider_lookup_returns_names_only_for_plan_managers(client, world, ctx, third, actors, login):  # noqa: F811
    found = client.get(f"{API}/composite-providers", params={"q": "provider p"}, headers=actors["A"]).json()
    assert {x["name"] for x in found} == {"Provider p1", "Provider p2", "Provider p3"} and all(set(x) == {"provider_id", "name"} for x in found)
    assert client.get(f"{API}/composite-providers", params={"q": "p"}, headers=actors["A"]).status_code == 422          # too short to browse
    assert client.get(f"{API}/composite-providers", params={"q": "%"}, headers=actors["A"]).status_code == 422
    assert client.get(f"{API}/composite-providers", params={"q": "zz"}, headers=actors["A"]).json() == []
    assert client.get(f"{API}/composite-providers", params={"q": "provider"}, headers=login(ctx["tphy"])).status_code == 403


# ------------------------------------------------------------------ response deadline

def test_deadline_must_be_sensible(client, world, ctx, third, actors):  # noqa: F811
    from datetime import datetime, timedelta, timezone
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    naive = (datetime.now() + timedelta(days=2)).isoformat()
    assert start(client, actors["A"], world, ctx, third, key="req-key-d001", respond_by=soon).status_code == 422
    assert start(client, actors["A"], world, ctx, third, key="req-key-d002", respond_by=naive).status_code == 422


def test_unanswered_invitations_expire_and_contributions_stay_usable(client, world, ctx, third, actors, db_factory):  # noqa: F811
    from datetime import datetime, timedelta, timezone

    from institute_common.context import service_context
    from institute_common.models.exams import CompositeRequest
    from inst_exam.services import composite_expiry
    deadline = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    body = start(client, actors["A"], world, ctx, third, key="req-key-d003", respond_by=deadline).json()
    assert body["respond_by"] and body["status"] == "OPEN"
    rid, s_b, s_c = body["request_id"], body["slots"][0]["slot_id"], body["slots"][1]["slot_id"]
    _, pv = publish(db_factory, world["p2"], world["p2b1"], ctx["phy"], "B Physics")
    assert client.post(f"{API}/composite-requests/{rid}/slots/{s_b}/contribute", json={"paper_version_id": str(pv)}, headers=actors["B"]).status_code == 200
    with db_factory() as db:                      # the deadline passes without C answering
        db.get(CompositeRequest, __import__("uuid").UUID(rid)).respond_by = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    _, pv_c = publish(db_factory, third["p"], third["b"], ctx["chem"], "C Chemistry")
    late = client.post(f"{API}/composite-requests/{rid}/slots/{s_c}/contribute", json={"paper_version_id": str(pv_c)}, headers=actors["C"])
    assert late.status_code == 409 and late.json()["detail"]["code"] == "EXPIRED"     # refused even before the sweep ran
    with service_context("exam"), db_factory() as db:
        assert composite_expiry.expire_due(db) == 1
        assert composite_expiry.expire_due(db) == 0                                     # idempotent
    detail = client.get(f"{API}/composite-requests/{rid}", headers=actors["A"]).json()
    assert detail["status"] == "EXPIRED"
    assert {s["slot_id"]: s["status"] for s in detail["slots"]} == {s_b: "CONTRIBUTED", s_c: "EXPIRED"}
    assert len(notices_for(client, actors["A"], "COMPOSITE_EXPIRED")) == 1
    assert len(notices_for(client, actors["C"], "COMPOSITE_EXPIRED")) == 1
    assert notices_for(client, actors["B"], "COMPOSITE_EXPIRED") == []                  # B answered in time
    # the contributed paper is still shared with A and usable in a plan
    from institute_common.models.papers import PaperShare
    with db_factory() as db:
        assert db.query(PaperShare).filter_by(shared_with_provider_id=world["p1"], status="ACTIVE").count() == 1
