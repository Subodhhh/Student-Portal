"""Retention of import review drafts (services/draft_retention.py): abandoned and discarded drafts are removed with the files only
they used; active, resumed and imported reviews and every file something else references are kept; one provider (and branch) per
run; a dry run changes nothing; repeating a run is harmless; the audit never holds a token or a decision's text."""
from datetime import timedelta
from uuid import UUID

import pytest

from institute_common.context import service_context
from institute_common.core.roles import Role
from institute_common.database import utcnow
from institute_common.ownership import attach_guard
from tests.test_paper_recognition import FLAGGED
from tests.test_papers import API, setup, tmp_store  # noqa: F401  (fixtures)
from tests.test_review_drafts import EDIT_1, KEEP_2, save, session, start


@pytest.fixture()
def admin(make_staff, world, login):
    return login(make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])[1])


def run(db_factory, provider, **kw):
    from inst_paper.services import draft_retention
    with service_context("paper"), db_factory() as db:
        attach_guard(db)                                     # the paper service may only change the tables it owns
        return draft_retention.cleanup(db, provider_id=provider, **kw)


def age(db_factory, draft_id, days, opened_days=None, status=None):
    """Make a draft look `days` old (last change), last opened `opened_days` ago (default: as old)."""
    from institute_common.models.papers import PaperReviewDraft
    with db_factory() as db:
        d = db.get(PaperReviewDraft, UUID(draft_id))
        d.created_at = d.updated_at = utcnow() - timedelta(days=days)
        d.last_opened_at = utcnow() - timedelta(days=days if opened_days is None else opened_days)
        if status:
            d.status = status
        db.commit()


def state(db_factory, draft_id):
    """(draft exists, its question file row exists, the file's bytes exist)."""
    from inst_paper.services.filestore import get_store
    from institute_common.models.papers import PaperReviewDraft, StoredFile
    with db_factory() as db:
        d = db.get(PaperReviewDraft, UUID(draft_id))
        fid = FILES[draft_id]
        sf = db.get(StoredFile, fid)
        return d is not None, sf is not None, (get_store().root / KEYS[fid]).exists()


FILES: dict[str, UUID] = {}
KEYS: dict[UUID, str] = {}


def new_draft(client, h, world, setup, db_factory, branch="p1b1", csv=FLAGGED):
    from institute_common.models.papers import PaperReviewDraft, StoredFile
    r = start(client, h, world, setup, csv=csv, branch=branch)
    assert r.status_code == 201, r.text
    draft_id = r.json()["draft"]["draft_id"]
    with db_factory() as db:
        fid = db.get(PaperReviewDraft, UUID(draft_id)).file_id
        FILES[draft_id], KEYS[fid] = fid, db.get(StoredFile, fid).storage_key
    return draft_id


def audits(db_factory):
    from institute_common.models.staff import AuditLog
    with db_factory() as db:
        return [(a.action, a.target_id, a.details) for a in db.query(AuditLog).filter(AuditLog.action.like("paper.review_draft.cleanup%"))
                .order_by(AuditLog.occurred_at)]


def test_an_abandoned_review_is_reported_by_a_dry_run_then_removed_with_its_file(client, world, setup, admin, db_factory):
    draft = new_draft(client, admin, world, setup, db_factory)
    save(client, admin, draft, 1, {"1:question": EDIT_1})
    age(db_factory, draft, 31)
    report = run(db_factory, world["p1"])                                       # dry run: the default
    assert [(i["draft_id"], i["reason"], i["inactive_days"]) for i in report["drafts"]] == [(draft, "abandoned", 31)]
    assert report["dry_run"] is True and state(db_factory, draft) == (True, True, True) and audits(db_factory) == []
    done = run(db_factory, world["p1"], dry_run=False)
    assert done["drafts"][0]["files_deleted"] == [str(FILES[draft])] and state(db_factory, draft) == (False, False, False)
    events = audits(db_factory)
    assert [e[0] for e in events] == ["paper.review_draft.cleanup", "paper.review_draft.cleanup_run"]
    assert events[0][1] == draft and events[0][2]["reason"] == "abandoned" and events[1][2]["removed"] == 1
    text = str(events)
    assert "∫" not in text and "token" not in text and "t" * 64 not in text      # never a decision's text or a review token


def test_a_review_still_in_use_is_kept(client, world, setup, admin, db_factory):
    changed = new_draft(client, admin, world, setup, db_factory)
    age(db_factory, changed, 29)                                                # changed 29 days ago: within the 30
    opened = new_draft(client, admin, world, setup, db_factory, csv=FLAGGED.replace("x = 1", "x = 2"))
    age(db_factory, opened, 60, opened_days=2)                                  # untouched for 60 days but opened 2 days ago
    resumed = new_draft(client, admin, world, setup, db_factory, csv=FLAGGED.replace("x = 1", "x = 3"))
    age(db_factory, resumed, 60)
    start(client, admin, world, setup, csv=FLAGGED.replace("x = 1", "x = 3"))   # the same file chosen again: resumed today
    assert run(db_factory, world["p1"], dry_run=False)["drafts"] == []
    assert all(state(db_factory, d) == (True, True, True) for d in (changed, opened, resumed))
    # opening it (a reload, "Continue") also counts as activity
    age(db_factory, changed, 45)
    client.get(f"{API}/review-drafts/{changed}", headers=admin)
    assert run(db_factory, world["p1"], dry_run=False)["drafts"] == []


def test_a_file_something_else_still_references_is_kept(client, world, setup, admin, db_factory):
    from institute_common.models.papers import PaperReviewDraft
    draft = new_draft(client, admin, world, setup, db_factory)
    save(client, admin, draft, 1, {"1:question": EDIT_1})
    # an attempted import (question 2 undecided) keeps a REJECTED upload with this very file, for its history
    assert client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 2}, headers=admin).status_code == 422
    # and another draft row sharing the file (a copy made directly: the constraint is on references, not on how they arose)
    with db_factory() as db:
        d = db.get(PaperReviewDraft, UUID(draft))
        twin = PaperReviewDraft(**{c.name: getattr(d, c.name) for c in d.__table__.columns if c.name != "draft_id"})
        twin.status = "DISCARDED"                                               # not OPEN: the unique index allows it
        db.add(twin)
        db.commit()
        twin_id = str(twin.draft_id)
    age(db_factory, draft, 40)
    done = run(db_factory, world["p1"], dry_run=False)
    item = next(i for i in done["drafts"] if i["draft_id"] == draft)
    assert item["files_deleted"] == [] and sorted(item["files_kept"][str(FILES[draft])]) == ["paper_review_drafts.file_id", "paper_uploads.file_id"]
    assert state(db_factory, draft) == (False, True, True)                     # the draft goes, the file stays
    assert twin_id not in [i["draft_id"] for i in done["drafts"]]               # discarded today: not due yet


def test_an_imported_review_and_its_file_are_never_removed(client, world, setup, admin, db_factory):
    draft = new_draft(client, admin, world, setup, db_factory)
    save(client, admin, draft, 1, {"1:question": EDIT_1, "2:question": KEEP_2})
    assert client.post(f"{API}/review-drafts/{draft}/import", json={"revision": 2}, headers=admin).status_code == 201
    age(db_factory, draft, 400)
    assert run(db_factory, world["p1"], dry_run=False)["drafts"] == []
    assert state(db_factory, draft) == (True, True, True)


def test_a_discarded_review_is_removed_after_its_short_window(client, world, setup, admin, db_factory):
    recent = new_draft(client, admin, world, setup, db_factory)
    old = new_draft(client, admin, world, setup, db_factory, csv=FLAGGED.replace("x = 1", "x = 2"))
    for d in (recent, old):
        client.post(f"{API}/review-drafts/{d}/discard", headers=admin)
    age(db_factory, recent, 3, status="DISCARDED")
    age(db_factory, old, 8, status="DISCARDED")
    done = run(db_factory, world["p1"], dry_run=False)
    assert [(i["draft_id"], i["reason"], i["status"]) for i in done["drafts"]] == [(old, "discarded", "DISCARDED")]
    assert state(db_factory, old) == (False, False, False) and state(db_factory, recent) == (True, True, True)


def test_repeating_a_cleanup_is_harmless(client, world, setup, admin, db_factory):
    from inst_paper.services.filestore import get_store
    first, second = new_draft(client, admin, world, setup, db_factory), new_draft(client, admin, world, setup, db_factory,
                                                                                    csv=FLAGGED.replace("x = 1", "x = 2"))
    age(db_factory, first, 31)
    age(db_factory, second, 31)
    get_store().delete(KEYS[FILES[second]])                                     # bytes already gone (an interrupted earlier run)
    done = run(db_factory, world["p1"], dry_run=False)
    assert sorted(i["draft_id"] for i in done["drafts"]) == sorted([first, second])
    assert {i["draft_id"]: i["bytes_missing"] for i in done["drafts"]} == {first: 0, second: 1}
    again = run(db_factory, world["p1"], dry_run=False)
    assert again["drafts"] == [] and again["skipped_in_use"] == 0
    assert [e[2]["removed"] for e in audits(db_factory) if e[0].endswith("_run")] == [2, 0]


def test_a_run_never_leaves_its_provider_or_branch(client, world, setup, admin, make_staff, login, db_factory):
    other_branch = new_draft(client, admin, world, setup, db_factory, branch="p1b2")
    here = new_draft(client, admin, world, setup, db_factory)
    pc, p2 = session(login, make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])[1])
    elsewhere = new_draft(pc, p2, world, setup, db_factory, branch="p2b1")
    for d in (other_branch, here, elsewhere):
        age(db_factory, d, 90)
    assert [i["draft_id"] for i in run(db_factory, world["p1"], branch_id=world["p1b1"], dry_run=False)["drafts"]] == [here]
    assert state(db_factory, other_branch)[0] and state(db_factory, elsewhere)[0]
    assert [i["draft_id"] for i in run(db_factory, world["p1"], dry_run=False)["drafts"]] == [other_branch]
    assert state(db_factory, elsewhere) == (True, True, True)                  # another provider's review: never considered
    assert [i["draft_id"] for i in run(db_factory, world["p2"], dry_run=False)["drafts"]] == [elsewhere]


@pytest.mark.parametrize("kw", [{"open_days": 6}, {"discarded_days": 0}])
def test_a_retention_shorter_than_the_minimum_is_refused(world, db_factory, kw):
    from inst_paper.services import draft_retention
    with pytest.raises(draft_retention.RetentionError):
        run(db_factory, world["p1"], dry_run=False, **kw)


def test_the_command_needs_a_provider_and_defaults_to_a_dry_run():
    from inst_paper import cleanup_review_drafts
    with pytest.raises(SystemExit) as exc:
        cleanup_review_drafts.main([])                                          # no provider: refused before anything runs
    assert exc.value.code == 2
    assert "--execute" in cleanup_review_drafts.__doc__ and "dry run (the default)" in cleanup_review_drafts.__doc__
