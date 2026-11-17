"""Recognition review drafts (paper service, migration 0007): an import review is saved as it is made and survives a reload; the
stored file is reused on import; two editors never silently overwrite each other; scope is the upload's (provider, branch,
subject; teachers their own drafts)."""
import io

import pytest

from institute_common.core.roles import Role
from tests.test_paper_recognition import CLEAN, FLAGGED
from tests.test_papers import API, setup, tmp_store  # noqa: F401  (fixtures)

EDIT_1 = {"action": "edit", "text": "Evaluate ∫₀^π sin x dx."}
KEEP_2 = {"action": "keep_original"}


@pytest.fixture()
def admin(make_staff, world, login):
    return login(make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b1"])])[1])


def session(login, code):
    """A user with their own browser (cookie jar): two people editing one draft at the same time."""
    from fastapi.testclient import TestClient
    from tests.gateway import app
    http = TestClient(app, base_url="http://localhost")
    return http, login(code, http=http)


def start(client, h, world, setup, csv=FLAGGED, branch="p1b1", title="Draft paper"):
    return client.post(f"{API}/branches/{world[branch]}/subjects/{setup['phy']}/review-drafts",
                       files={"file": ("paper.csv", io.BytesIO(csv.encode()), "text/csv")}, data={"title": title}, headers=h)


def save(client, h, draft_id, base, changes):
    return client.patch(f"{API}/review-drafts/{draft_id}", json={"base_revision": base, "changes": changes}, headers=h)


def counts(db_factory):
    from institute_common.models.papers import PaperReviewDraft, PaperVersion, StoredFile
    with db_factory() as db:
        return tuple(db.query(m).count() for m in (PaperReviewDraft, PaperVersion, StoredFile))


def test_a_draft_keeps_the_file_and_the_decisions_across_a_reload(client, world, setup, admin, db_factory):
    r = start(client, admin, world, setup)
    assert r.status_code == 201, r.text
    body = r.json()
    draft = body["draft"]
    assert body["resumed"] is False and draft["status"] == "OPEN" and draft["revision"] == 1 and draft["file_name"] == "paper.csv"
    assert body["preview"]["summary"]["questions_needing_review"] == 2
    assert counts(db_factory) == (1, 0, 1)                                     # the file is kept; nothing is imported
    saved = save(client, admin, draft["draft_id"], 1, {"1:question": EDIT_1})
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    # a reload: the draft comes back with its decisions and a preview read again from the stored file
    again = client.get(f"{API}/review-drafts/{draft['draft_id']}", headers=admin).json()
    assert again["draft"]["decisions"]["1:question"]["decision"] == EDIT_1 and again["draft"]["revision"] == 2
    assert again["preview"]["questions"][0]["fields"]["question"]["original"] == "Evaluate ∫₀π sin x dx."
    assert again["engine_changed"] is False
    # previewing the same file again continues the same draft (title may change); it is listed for the branch
    resumed = start(client, admin, world, setup, title="Renamed")
    assert resumed.status_code == 201 and resumed.json()["resumed"] is True
    assert resumed.json()["draft"]["draft_id"] == draft["draft_id"] and resumed.json()["draft"]["title"] == "Renamed"
    listed = client.get(f"{API}/review-drafts", params={"branch_id": str(world["p1b1"])}, headers=admin).json()["items"]
    assert [(d["draft_id"], d["decided"]) for d in listed] == [(draft["draft_id"], 1)] and "decisions" not in listed[0]
    assert counts(db_factory) == (1, 0, 1)


def test_two_editors_never_overwrite_each_other(client, world, setup, admin, make_staff, login):
    oc, other = session(login, make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])[1])
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    assert save(client, admin, draft, 1, {"1:question": EDIT_1}).json()["revision"] == 2
    # the other editor still has revision 1: a different field is saved, the same field is refused with the current value
    assert save(oc, other, draft, 1, {"2:question": KEEP_2}).json()["revision"] == 3
    clash = save(oc, other, draft, 1, {"1:question": {"action": "keep_original"}})
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "DRAFT_CONFLICT"
    assert clash.json()["detail"]["conflicts"]["1:question"]["decision"] == EDIT_1 and clash.json()["detail"]["revision"] == 3
    kept = client.get(f"{API}/review-drafts/{draft}", headers=admin).json()["draft"]["decisions"]
    assert kept["1:question"]["decision"] == EDIT_1                            # nothing was overwritten
    # having seen it, the other editor may replace it deliberately; clearing a decision is a change like any other
    assert save(oc, other, draft, 3, {"1:question": {"action": "keep_original"}}).status_code == 200
    assert save(client, admin, draft, 4, {"2:question": None}).json()["decisions"]["2:question"]["decision"] is None


@pytest.mark.parametrize("changes", [{"9:nope": KEEP_2}, {"1:question": {"action": "edit", "text": "  "}},
                                     {"1:question": {"action": "rewrite"}}, {"1:question": {"action": "keep_original", "x": 1}}])
def test_invalid_decisions_are_refused(client, world, setup, admin, changes):
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    assert save(client, admin, draft, 1, changes).status_code == 422
    assert client.get(f"{API}/review-drafts/{draft}", headers=admin).json()["draft"]["revision"] == 1


def test_importing_a_draft_uses_the_stored_file_and_closes_the_draft(client, world, setup, admin, db_factory):
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    save(client, admin, draft, 1, {"1:question": EDIT_1})
    blocked = client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 2}, headers=admin)
    assert blocked.status_code == 422 and blocked.json()["detail"]["code"] == "REVIEW_REQUIRED"   # question 2 still undecided
    assert client.get(f"{API}/review-drafts/{draft}", headers=admin).json()["draft"]["status"] == "OPEN"
    save(client, admin, draft, 2, {"2:question": KEEP_2})
    stale = client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 2}, headers=admin)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DRAFT_CONFLICT"     # a newer save must be seen first
    files_before = counts(db_factory)[2]
    r = client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 3}, headers=admin)
    assert r.status_code == 201, r.text
    stems = [q["stem"] for q in client.get(f"{API}/paper-versions/{r.json()['paper_version_id']}", headers=admin).json()["questions"]]
    assert stems == [r"Evaluate \(\int_{0}^{\pi} \sin x\,dx\).", "Simplify sin x/x at x = 1."]
    assert counts(db_factory)[2] == files_before                               # the draft's stored file, not a new copy
    closed = client.get(f"{API}/review-drafts/{draft}", headers=admin).json()
    assert closed["draft"]["status"] == "IMPORTED" and closed["draft"]["result_paper_version_id"] == r.json()["paper_version_id"]
    assert "preview" not in closed
    assert save(client, admin, draft, 3, {"1:question": KEEP_2}).json()["detail"]["code"] == "DRAFT_CLOSED"
    assert client.get(f"{API}/review-drafts", params={"branch_id": str(world["p1b1"])}, headers=admin).json()["items"] == []
    # the paper may then be reviewed again from scratch: a new draft for the same file
    assert start(client, admin, world, setup).json()["resumed"] is False


def test_a_discarded_draft_is_closed(client, world, setup, admin):
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    assert client.post(f"{API}/review-drafts/{draft}/discard", headers=admin).json()["draft"]["status"] == "DISCARDED"
    assert client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 1}, headers=admin).json()["detail"]["code"] == "DRAFT_CLOSED"
    assert client.get(f"{API}/review-drafts", params={"branch_id": str(world["p1b1"])}, headers=admin).json()["items"] == []


def test_an_unreadable_file_is_never_kept(client, world, setup, admin, db_factory):
    r = start(client, admin, world, setup, csv="question,option_a\nonly,one\n")
    assert r.status_code == 422 and counts(db_factory) == (0, 0, 0)


def test_drafts_are_scoped_like_uploads(client, world, setup, admin, make_staff, login):
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    bc, other_branch = session(login, make_staff([(Role.BRANCH_ADMIN, world["p1"], world["p1b2"])])[1])
    fc, foreign = session(login, make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])[1])
    assert bc.get(f"{API}/review-drafts/{draft}", headers=other_branch).status_code == 403
    assert save(bc, other_branch, draft, 1, {"2:question": KEEP_2}).status_code == 403
    assert fc.get(f"{API}/review-drafts/{draft}", headers=foreign).status_code == 404            # another provider's draft
    assert save(fc, foreign, draft, 1, {"2:question": KEEP_2}).status_code == 404
    assert fc.post(f"{API}/review-drafts/{draft}/import", json={"revision": 1}, headers=foreign).status_code == 404
    assert fc.post(f"{API}/review-drafts/{draft}/discard", headers=foreign).status_code == 404
    assert fc.get(f"{API}/review-drafts", params={"branch_id": str(world["p1b1"])}, headers=foreign).status_code == 403
    assert start(fc, foreign, world, setup).status_code == 403
    assert client.get(f"{API}/review-drafts/{draft}", headers=admin).json()["draft"]["revision"] == 1   # untouched


def test_teachers_continue_only_their_own_drafts(client, world, setup, admin, login):
    (tc, teacher), (oc, other_teacher) = session(login, setup["tcode"]), session(login, setup["ocode"])
    mine = start(tc, teacher, world, setup).json()["draft"]["draft_id"]
    theirs = start(oc, other_teacher, world, setup).json()                     # the same file: each teacher has their own draft
    assert theirs["resumed"] is False and theirs["draft"]["draft_id"] != mine
    assert oc.get(f"{API}/review-drafts/{mine}", headers=other_teacher).status_code == 403
    assert save(oc, other_teacher, mine, 1, {"2:question": KEEP_2}).status_code == 403
    assert save(tc, teacher, mine, 1, {"2:question": KEEP_2}).status_code == 200
    listed = oc.get(f"{API}/review-drafts", params={"branch_id": str(world["p1b1"])}, headers=other_teacher).json()["items"]
    assert [d["draft_id"] for d in listed] == [theirs["draft"]["draft_id"]]
    # the branch admin sees and may continue both
    both = client.get(f"{API}/review-drafts", params={"branch_id": str(world["p1b1"])}, headers=admin).json()["items"]
    assert {d["draft_id"] for d in both} == {mine, theirs["draft"]["draft_id"]}
    assert save(client, admin, mine, 2, {"1:question": EDIT_1}).status_code == 200


def test_every_change_is_audited_without_its_text(client, world, setup, admin, make_staff, login, db_factory):
    from institute_common.models.staff import AuditLog
    oc, other = session(login, make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])[1])
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    save(client, admin, draft, 1, {"1:question": EDIT_1})
    save(oc, other, draft, 1, {"1:question": KEEP_2})                          # a conflict
    client.post(f"{API}/review-drafts/{draft}/discard", headers=admin)
    with db_factory() as db:
        rows = db.query(AuditLog).filter(AuditLog.action.like("paper.review_draft.%")).order_by(AuditLog.occurred_at).all()
        events = [(a.action, a.outcome) for a in rows]
        assert events == [("paper.review_draft.create", "SUCCESS"), ("paper.review_draft.save", "SUCCESS"),
                          ("paper.review_draft.save", "FAILURE"), ("paper.review_draft.discard", "SUCCESS")]
        assert all(a.target_id == draft and a.provider_id == world["p1"] for a in rows)
        assert rows[1].details["fields"] == ["1:question"] and "∫" not in str([a.details for a in rows])   # never the text
        assert rows[2].details["conflict"] == ["1:question"]


def test_a_clean_file_also_gets_a_draft_and_imports_without_decisions(client, world, setup, admin):
    draft = start(client, admin, world, setup, csv=CLEAN).json()["draft"]
    r = client.post(f"{API}/review-drafts/{draft['draft_id']}/import", json={"revision": draft["revision"]}, headers=admin)
    assert r.status_code == 201, r.text


def test_decisions_made_with_an_older_engine_must_be_confirmed_again(client, world, setup, admin, monkeypatch):
    from inst_paper.services import recognition
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    save(client, admin, draft, 1, {"1:question": EDIT_1, "2:question": KEEP_2})
    monkeypatch.setattr(recognition, "ENGINE_VERSION", "9.9.9")               # the engine is improved while the review waits
    opened = client.get(f"{API}/review-drafts/{draft}", headers=admin).json()
    assert opened["engine_changed"] is True and opened["stale_keys"] == ["1:question", "2:question"]
    assert opened["draft"]["decisions"]["1:question"]["decision"] == EDIT_1  # kept, to be confirmed - never dropped
    refused = client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 2}, headers=admin)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "REVIEW_STALE"
    assert refused.json()["detail"]["stale_keys"] == ["1:question", "2:question"]
    save(client, admin, draft, 2, {"1:question": EDIT_1})                    # confirmed one by one
    assert client.get(f"{API}/review-drafts/{draft}", headers=admin).json()["stale_keys"] == ["2:question"]
    save(client, admin, draft, 3, {"2:question": KEEP_2})
    assert client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 4}, headers=admin).status_code == 201


def test_opening_a_review_records_it_as_active(client, world, setup, admin, db_factory):
    from institute_common.models.papers import PaperReviewDraft
    draft = start(client, admin, world, setup).json()["draft"]["draft_id"]
    with db_factory() as db:
        first = db.get(PaperReviewDraft, __import__("uuid").UUID(draft)).last_opened_at
    client.get(f"{API}/review-drafts/{draft}", headers=admin)
    with db_factory() as db:
        d = db.get(PaperReviewDraft, __import__("uuid").UUID(draft))
        assert first is not None and d.last_opened_at >= first and d.revision == 1      # opened, not changed
