"""Global Test contributions: provider staff offer their own approved papers for a published Global Test, the platform approves or
rejects them, an approved paper is copied (idempotently, through the paper service's internal API) into an independent GLOBAL paper
that still needs the normal approval, conflicts for one subject are resolved explicitly, and the published test never changes."""
from uuid import UUID

import pytest

from institute_common.core.roles import Role
from institute_common.internal_http import InternalServiceError
from inst_exam.routes import global_contributions as gc_mod
from tests.test_exams import make_paper
from tests.test_question_bank import bank, create, q_body  # noqa: F401  (fixture)
from tests.test_review_fixes import gdt  # noqa: F401  (fixture)

API = "/api/v1"


@pytest.fixture()
def gc(client, world, bank, gdt, make_staff, login, db_factory):  # noqa: F811
    """A published Physics Global Test (gdt); providers p1 and p2 each with an approved Chemistry paper; p1 also a Physics paper, a
    draft (VALIDATED) Chemistry paper and a branch-2 paper; a Branch Admin of p1-b1; the bank's teacher teaches Physics in p1-b1."""
    c = dict(bank)
    c["test_id"] = gdt["test_id"]
    _, c["ba"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])
    _, c["ba2"] = make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])
    c["p1_chem"], c["p1_chem_v"] = make_paper(db_factory, world["p1"], world["p1b1"], bank["chem"], "P1 Chemistry", "PUBLISHED", n=3)
    c["p2_chem"], c["p2_chem_v"] = make_paper(db_factory, world["p2"], world["p2b1"], bank["chem"], "P2 Chemistry", "APPROVED", n=2)
    c["p1_phy"], c["p1_phy_v"] = make_paper(db_factory, world["p1"], world["p1b1"], bank["phy"], "P1 Physics", "PUBLISHED", n=2)
    _, c["p1_draft_v"] = make_paper(db_factory, world["p1"], world["p1b1"], bank["chem"], "P1 Draft", "VALIDATED", n=2)
    _, c["p1b2_chem_v"] = make_paper(db_factory, world["p1"], world["p1b2"], bank["chem"], "P1 B2 Chemistry", "PUBLISHED", n=2)
    return c


@pytest.fixture()
def login(login):  # noqa: F811
    """One logged-in HTTP client per staff member (the MFA code limit allows only a few logins per account): `login(code)` returns
    headers as usual, and requests made with those headers through `client` use that member's own session."""
    from fastapi.testclient import TestClient
    from tests.gateway import app
    sessions: dict = {}

    def _login(code):
        if code not in sessions:
            http = TestClient(app, base_url="http://localhost")
            sessions[code] = (http, login(code, http=http))
        return sessions[code][1]
    _login.sessions = sessions
    return _login


@pytest.fixture()
def client(client, login):  # noqa: F811
    """Routes each request to the session of the member whose headers it carries."""
    class Router:
        def _pick(self, headers):
            for http, h in login.sessions.values():
                if headers and h.get("X-CSRF-Token") == headers.get("X-CSRF-Token"):
                    return http
            return client

        def get(self, url, headers=None, **kw):
            return self._pick(headers).get(url, headers=headers, **kw)

        def post(self, url, headers=None, **kw):
            return self._pick(headers).post(url, headers=headers, **kw)

        def put(self, url, headers=None, **kw):
            return self._pick(headers).put(url, headers=headers, **kw)
    return Router()


def submit(client, login, gc, who, version_key, note=None):
    return client.post(f"{API}/global-tests/{gc['test_id']}/contributions", json={"paper_version_id": str(gc[version_key]), "note": note},
                       headers=login(gc[who]))


def contributions(db_factory):
    from institute_common.models.exams import GlobalTestContribution
    with db_factory() as db:
        return db.query(GlobalTestContribution).order_by(GlobalTestContribution.submitted_at).all()


def sections(db_factory, test_id):
    from institute_common.models.exams import GlobalTestSection
    with db_factory() as db:
        return [(s.subject_id, s.paper_version_id) for s in db.query(GlobalTestSection).filter_by(test_id=UUID(test_id)).order_by(GlobalTestSection.position)]


# ------------------------------------------------------------------ submission

def test_provider_staff_contribute_only_their_own_approved_papers(client, world, gc, login, db_factory):
    options = client.get(f"{API}/global-tests/{gc['test_id']}/contribution-options", headers=login(gc["pa1"])).json()["items"]
    assert {o["paper_title"] for o in options} == {"P1 Chemistry", "P1 Physics", "P1 B2 Chemistry"}       # never p2's, never drafts
    r = submit(client, login, gc, "pa1", "p1_chem_v", note="Fresh set")
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["status"], c["subject"], c["provider_name"], c["branch_name"], c["note"]) == ("PENDING", "Chemistry", "Provider p1", "p1-b1", "Fresh set")
    assert c["competing"] is None and c["section_exists"] is None                                           # no cross-provider context
    again = submit(client, login, gc, "pa1", "p1_chem_v")
    assert again.json()["contribution_id"] == c["contribution_id"] and len(contributions(db_factory)) == 1   # idempotent
    other = submit(client, login, gc, "pa1", "p1b2_chem_v")                                                  # second paper, same subject
    assert other.status_code == 409 and other.json()["detail"]["code"] == "ALREADY_CONTRIBUTED"


@pytest.mark.parametrize("who,version,status", [
    ("pa2", "p1_chem_v", 404),          # another provider's paper: indistinguishable from a missing one
    ("teacher", "p1_chem_v", 404),      # a subject the teacher does not teach
    ("ba", "p1b2_chem_v", 404),         # another branch's paper
    ("ba2", "p1_chem_v", 404),
    ("pa1", "p1_draft_v", 409),         # not approved yet
])
def test_unauthorized_or_unapproved_papers_are_refused(client, gc, login, db_factory, who, version, status):
    r = submit(client, login, gc, who, version)
    assert r.status_code == status, r.text
    assert contributions(db_factory) == []


def test_teachers_and_branch_admins_contribute_within_their_scope(client, gc, login):
    assert submit(client, login, gc, "teacher", "p1_phy_v").status_code == 201        # teaches Physics in p1-b1
    assert submit(client, login, gc, "ba", "p1_chem_v").status_code == 201


def test_draft_or_withdrawn_tests_take_no_contributions(client, gc, login):
    h = login(gc["sa"])
    draft = client.post(f"{API}/global-tests/{gc['test_id']}/derive", headers=h).json()
    r = client.post(f"{API}/global-tests/{draft['test_id']}/contributions", json={"paper_version_id": str(gc["p1_chem_v"])}, headers=login(gc["pa1"]))
    assert r.status_code == 404                                                        # providers never see drafts
    client.post(f"{API}/global-tests/{gc['test_id']}/withdraw", json={"reason": "Replaced"}, headers=login(gc["sa"]))
    assert submit(client, login, gc, "pa1", "p1_chem_v").status_code == 404


def test_contributions_are_visible_only_within_scope(client, gc, login):
    submit(client, login, gc, "pa1", "p1_chem_v")
    submit(client, login, gc, "teacher", "p1_phy_v")
    submit(client, login, gc, "pa2", "p2_chem_v")

    def seen(who):
        return sorted(x["paper_title"] for x in client.get(f"{API}/global-test-contributions", headers=login(gc[who])).json()["items"])
    assert seen("sa") == ["P1 Chemistry", "P1 Physics", "P2 Chemistry"]
    assert seen("pa1") == ["P1 Chemistry", "P1 Physics"] and seen("pa2") == ["P2 Chemistry"]
    assert seen("teacher") == ["P1 Physics"]                                           # own submission / taught subject only
    platform = {x["paper_title"]: x for x in client.get(f"{API}/global-test-contributions", headers=login(gc["sa"])).json()["items"]}
    assert platform["P1 Chemistry"]["competing"] == 2 and platform["P1 Physics"]["section_exists"] is True


# ------------------------------------------------------------------ review, copy, conflicts

def test_approval_copies_the_paper_and_never_changes_the_published_test(client, world, gc, login, db_factory):
    from institute_common.models.exams import ExamNotificationOutbox
    from institute_common.models.papers import GlobalPaperCopy, Paper, PaperVersion, PaperVersionQuestion
    before = sections(db_factory, gc["test_id"])
    cid = submit(client, login, gc, "pa1", "p1_chem_v").json()["contribution_id"]
    assert client.post(f"{API}/global-test-contributions/{cid}/approve", json={}, headers=login(gc["pa1"])).status_code == 403
    r = client.post(f"{API}/global-test-contributions/{cid}/approve", json={"note": "Good set"}, headers=login(gc["sa"]))
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["status"], c["copy_status"], c["global_version_status"], c["review_note"]) == ("APPROVED", "COPIED", "VALIDATED", "Good set")
    with db_factory() as db:
        gp = db.get(Paper, UUID(c["global_paper_id"]))
        assert gp.scope == "GLOBAL" and gp.provider_id is None and gp.subject_id == gc["chem"]
        gv = db.get(PaperVersion, UUID(c["global_paper_version_id"]))
        assert gv.status == "VALIDATED" and gv.question_count == 3                    # still needs the normal paper approval
        assert db.query(PaperVersionQuestion).filter_by(paper_version_id=gv.paper_version_id).count() == 3
        assert db.get(PaperVersion, gc["p1_chem_v"]).status == "PUBLISHED"           # the source is untouched
        assert db.query(GlobalPaperCopy).count() == 1
        kinds = [o.payload["kind"] for o in db.query(ExamNotificationOutbox)]
        assert "GLOBAL_CONTRIBUTION_SUBMITTED" in kinds and "GLOBAL_CONTRIBUTION_APPROVED" in kinds
    assert sections(db_factory, gc["test_id"]) == before                               # the published test is unchanged
    again = client.post(f"{API}/global-test-contributions/{cid}/retry-copy", headers=login(gc["sa"])).json()
    assert again["global_paper_version_id"] == c["global_paper_version_id"]
    with db_factory() as db:
        assert db.query(GlobalPaperCopy).count() == 1 and db.query(Paper).filter_by(scope="GLOBAL").count() == 2   # gdt's paper + this copy


def test_competing_contributions_are_a_conflict_resolved_explicitly(client, gc, login, db_factory):
    first = submit(client, login, gc, "pa1", "p1_chem_v").json()["contribution_id"]
    second = submit(client, login, gc, "pa2", "p2_chem_v").json()["contribution_id"]
    assert client.post(f"{API}/global-test-contributions/{first}/approve", json={}, headers=login(gc["sa"])).status_code == 200
    r = client.post(f"{API}/global-test-contributions/{second}/approve", json={}, headers=login(gc["sa"]))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SUBJECT_CONFLICT" and r.json()["detail"]["approved_contribution_id"] == first
    assert client.post(f"{API}/global-test-contributions/{second}/reject", json={"reason": ""}, headers=login(gc["sa"])).status_code == 422
    r = client.post(f"{API}/global-test-contributions/{second}/reject", json={"reason": "Chemistry already covered"}, headers=login(gc["sa"]))
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert client.post(f"{API}/global-test-contributions/{second}/approve", json={}, headers=login(gc["sa"])).status_code == 409
    assert [c.status for c in contributions(db_factory)] == ["APPROVED", "REJECTED"]


def test_the_database_allows_one_approved_contribution_per_subject(db_factory, world, gc):
    """The final rule holds even if two approvals race past the application checks."""
    from sqlalchemy.exc import IntegrityError
    from institute_common.models.exams import GlobalTestContribution
    common = dict(test_id=UUID(gc["test_id"]), subject_id=gc["chem"], submitted_by=UUID(int=1), status="APPROVED")
    with db_factory() as db:
        db.add(GlobalTestContribution(provider_id=world["p1"], branch_id=world["p1b1"], paper_version_id=gc["p1_chem_v"], **common))
        db.commit()
        db.add(GlobalTestContribution(provider_id=world["p2"], branch_id=world["p2b1"], paper_version_id=gc["p2_chem_v"], **common))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_copy_failure_is_recorded_and_retried_safely(client, gc, login, db_factory, monkeypatch):
    from institute_common.models.papers import GlobalPaperCopy
    cid = submit(client, login, gc, "pa1", "p1_chem_v").json()["contribution_id"]

    class Down:
        def request(self, *a, **k):
            raise InternalServiceError("service unavailable")
    monkeypatch.setattr(gc_mod, "peer", lambda name: Down())
    r = client.post(f"{API}/global-test-contributions/{cid}/approve", json={}, headers=login(gc["sa"]))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "COPY_FAILED"
    [c] = contributions(db_factory)
    assert (c.status, c.copy_status) == ("APPROVED", "FAILED") and "PAPER_SERVICE_UNAVAILABLE" in c.copy_error

    class Garbled:                                         # a 201 without the ids is not trusted
        def request(self, *a, **k):
            import httpx
            return httpx.Response(201, json={"created": True})
    monkeypatch.setattr(gc_mod, "peer", lambda name: Garbled())
    assert client.post(f"{API}/global-test-contributions/{cid}/retry-copy", headers=login(gc["sa"])).status_code == 409
    monkeypatch.undo()
    ok = client.post(f"{API}/global-test-contributions/{cid}/retry-copy", headers=login(gc["sa"]))
    assert ok.status_code == 200 and ok.json()["copy_status"] == "COPIED" and ok.json()["copy_error"] is None
    with db_factory() as db:
        assert db.query(GlobalPaperCopy).count() == 1


def test_the_paper_service_rechecks_the_source_before_copying(client, gc, login, db_factory):
    """The internal copy API re-validates ownership itself; a mismatching request is refused and nothing is created."""
    from institute_common.internal_http import peer
    from institute_common.models.papers import GlobalPaperCopy
    body = {"contribution_id": str(UUID(int=7)), "source_paper_version_id": str(gc["p1_chem_v"]), "expected_provider_id": str(gc["test_id"]),
            "expected_branch_id": str(gc["test_id"]), "expected_subject_id": str(gc["chem"]), "copied_by": str(UUID(int=1)),
            "authorization_reference": "test"}
    r = peer("paper").request("POST", "/papers/copy-global", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOURCE_SCOPE_MISMATCH"
    with db_factory() as db:
        assert db.query(GlobalPaperCopy).count() == 0


def test_withdrawal_rules(client, gc, login):
    cid = submit(client, login, gc, "teacher", "p1_phy_v").json()["contribution_id"]
    assert client.post(f"{API}/global-test-contributions/{cid}/withdraw", json={"reason": "x"}, headers=login(gc["pa2"])).status_code == 404
    r = client.post(f"{API}/global-test-contributions/{cid}/withdraw", json={"reason": "Wrong paper"}, headers=login(gc["pa1"]))
    assert r.status_code == 200 and r.json()["status"] == "WITHDRAWN"                  # an admin of the contributing provider
    assert submit(client, login, gc, "teacher", "p1_phy_v").status_code == 201         # may be offered again after withdrawal
    again = [x for x in client.get(f"{API}/global-test-contributions?status=PENDING", headers=login(gc["sa"])).json()["items"]][0]
    assert again["can_withdraw"] is False                                              # the platform rejects; it never withdraws
    assert client.post(f"{API}/global-test-contributions/{again['contribution_id']}/withdraw", json={"reason": "Platform tries"},
                       headers=login(gc["sa"])).status_code == 403
    cid2 = submit(client, login, gc, "pa1", "p1_chem_v").json()["contribution_id"]
    client.post(f"{API}/global-test-contributions/{cid2}/approve", json={}, headers=login(gc["sa"]))
    assert client.post(f"{API}/global-test-contributions/{cid2}/withdraw", json={"reason": "Changed our mind"}, headers=login(gc["pa1"])).status_code == 409


def test_an_approved_copy_reaches_students_only_through_a_new_platform_draft(client, world, gc, login, db_factory):
    """Copy -> normal paper approval -> a DERIVED draft edited by the platform -> publish. The original published test keeps its
    sections; two papers for one subject are refused."""
    cid = submit(client, login, gc, "pa1", "p1_chem_v").json()["contribution_id"]
    c = client.post(f"{API}/global-test-contributions/{cid}/approve", json={}, headers=login(gc["sa"])).json()
    h = login(gc["sa"])
    gv = c["global_paper_version_id"]
    original = sections(db_factory, gc["test_id"])
    draft = client.post(f"{API}/global-tests/{gc['test_id']}/derive", headers=h).json()
    body = {"name": "Daily #2", "duration_minutes": 45, "sections": [{"paper_version_id": str(original[0][1])}, {"paper_version_id": gv}]}
    r = client.put(f"{API}/global-tests/{draft['test_id']}", json=body, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "VERSION_NOT_APPROVED"            # the copy must be approved first
    assert client.post(f"{API}/paper-versions/{gv}/approve", json={"note": "checked"}, headers=h).status_code == 200
    assert client.post(f"{API}/paper-versions/{gv}/publish", json={}, headers=h).status_code == 200
    r = client.put(f"{API}/global-tests/{draft['test_id']}", json=body, headers=h)
    assert r.status_code == 200 and [s["subject"] for s in r.json()["sections"]] == ["Physics", "Chemistry"]
    qid = create(client, login, gc["sa"], q_body(gc["chem"], scope="GLOBAL", stem="Platform chemistry?"))
    h = login(gc["sa"])
    other_chem = client.post(f"{API}/papers", json={"scope": "GLOBAL", "subject_id": str(gc["chem"]), "title": "Platform chem", "question_ids": [qid]},
                             headers=h).json()["paper_version_id"]
    client.post(f"{API}/paper-versions/{other_chem}/approve", json={}, headers=h)
    dup = client.put(f"{API}/global-tests/{draft['test_id']}", json={**body, "sections": body["sections"] + [{"paper_version_id": str(other_chem)}]}, headers=h)
    assert dup.status_code == 422 and dup.json()["detail"]["code"] == "DUPLICATE_SUBJECT"
    assert client.post(f"{API}/global-tests/{draft['test_id']}/publish", headers=h).json()["status"] == "PUBLISHED"
    assert sections(db_factory, gc["test_id"]) == original
