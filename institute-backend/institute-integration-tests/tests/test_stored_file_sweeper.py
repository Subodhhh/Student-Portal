"""Stored-file sweeper (services/file_sweeper.py): unreferenced stored_files records of one provider, and bytes in the store with
no record, older than a minimum age - dry run by default, never a referenced, young, foreign or unknown file, safe under a reference
appearing mid-run, missing bytes, filesystem errors, repeats and an interrupted earlier cleanup."""
import io
import os
import time
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from institute_common.context import service_context
from institute_common.core.roles import Role
from institute_common.database import utcnow
from institute_common.ownership import attach_guard
from tests.test_papers import API, GOOD, setup, tmp_store  # noqa: F401  (fixtures)
from tests.test_paper_recognition import FLAGGED
from tests.test_review_drafts import start

KEY_CSV = "number,answer\n1,D\n2,A\n"
GOOD_NO_ANSWERS = GOOD.replace(",correct,", ",").replace(",D,4,1,", ",4,1,").replace(",A,4,1,", ",4,1,")


@pytest.fixture()
def admin(make_staff, world, login):
    return login(make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])[1])


def store():
    from inst_paper.services.filestore import get_store
    return get_store()


def sweep(db_factory, mode="records", **kw):
    from inst_paper.services import file_sweeper
    with service_context("paper"), db_factory() as db:
        attach_guard(db)                                   # the paper service may only change the tables it owns
        if mode == "records":
            return file_sweeper.sweep_records(db, **kw)
        return file_sweeper.sweep_unowned_bytes(db, **kw)


def stored(db_factory, provider, days=10, data=b"orphan"):
    """A stored file nothing references (as a replaced photo leaves it), `days` old."""
    from institute_common.models.papers import StoredFile
    key, sha = store().save(data)
    with db_factory() as db:
        sf = StoredFile(provider_id=provider, storage_key=key, original_name="page.jpg", content_type="image/jpeg", size_bytes=len(data),
                        sha256=sha, uploaded_by=uuid4(), created_at=utcnow() - timedelta(days=days))
        db.add(sf)
        db.commit()
        return sf.file_id, key


def exists(db_factory, file_id, key):
    from institute_common.models.papers import StoredFile
    with db_factory() as db:
        return db.get(StoredFile, file_id) is not None, (store().root / key).exists()


def age_all(db_factory, days):
    from institute_common.models.papers import StoredFile
    with db_factory() as db:
        for sf in db.query(StoredFile):
            sf.created_at = utcnow() - timedelta(days=days)
        db.commit()


def old_bytes(key, days=10):
    t = time.time() - days * 86400
    os.utime(store().root / key, (t, t))


def events(db_factory):
    from institute_common.models.staff import AuditLog
    with db_factory() as db:
        return [(a.action, a.details) for a in db.query(AuditLog).filter(AuditLog.action.like("paper.stored_file.%")).order_by(AuditLog.occurred_at)]


def test_an_orphaned_record_is_reported_by_a_dry_run_then_deleted(client, world, db_factory):   # client: audit forwarding
    fid, key = stored(db_factory, world["p1"])
    first = sweep(db_factory, provider_id=world["p1"])
    again = sweep(db_factory, provider_id=world["p1"])
    assert first["files"] == again["files"] == [{"file_id": str(fid), "provider_id": str(world["p1"]), "age_days": 10,
                                                 "reason": "unreferenced", "outcome": "would_delete"}]
    assert exists(db_factory, fid, key) == (True, True) and events(db_factory) == []          # a dry run changes and records nothing
    done = sweep(db_factory, provider_id=world["p1"], dry_run=False)
    assert done["files"][0]["outcome"] == "deleted" and exists(db_factory, fid, key) == (False, False)
    assert [e[0] for e in events(db_factory)] == ["paper.stored_file.sweep", "paper.stored_file.sweep_run"]
    assert events(db_factory)[0][1] == {"file_id": str(fid), "age_days": 10, "reason": "unreferenced", "outcome": "deleted"}
    assert sweep(db_factory, provider_id=world["p1"], dry_run=False)["files"] == []          # repeated: nothing left to do


def test_a_young_file_is_never_a_candidate(world, db_factory):
    fid, key = stored(db_factory, world["p1"], days=2)
    report = sweep(db_factory, provider_id=world["p1"], dry_run=False)
    assert report["files"] == [] and report["younger"] == 1 and exists(db_factory, fid, key) == (True, True)
    assert sweep(db_factory, provider_id=world["p1"], min_age_days=1, dry_run=False)["files"][0]["outcome"] == "deleted"


def test_every_referencing_table_keeps_its_files(client, world, setup, admin, db_factory):
    from inst_paper.services import file_refs
    from institute_common.models.papers import PhotoDraft, PhotoDraftFile, StoredFile
    # the references the schema has today: a new one must be added here on purpose (and is then covered by the generic check)
    assert {f"{t.name}.{c.name}" for t, c in file_refs.referencing_columns()} == {
        "paper_uploads.file_id", "paper_uploads.answer_key_file_id", "paper_photo_draft_files.file_id",
        "paper_review_drafts.file_id", "paper_review_drafts.answer_key_file_id"}
    # an imported paper (question file + separate answer key): its upload keeps both files; its versions reach them through it
    r = client.post(f"{API}/branches/{world['p1b1']}/subjects/{setup['phy']}/papers/upload", headers=admin, data={"title": "Imported"},
                    files={"file": ("p.csv", io.BytesIO(GOOD_NO_ANSWERS.encode()), "text/csv"), "answer_key": ("k.csv", io.BytesIO(KEY_CSV.encode()), "text/csv")})
    assert r.status_code == 201, r.text
    # a rejected upload keeps its file for its history; a review draft (with an answer key) keeps both of its files
    bad = client.post(f"{API}/branches/{world['p1b1']}/subjects/{setup['phy']}/papers/upload", headers=admin, data={"title": "Rejected"},
                      files={"file": ("bad.csv", io.BytesIO(b"question,option_a\nonly,one\n"), "text/csv")})
    assert bad.status_code == 422
    draft = client.post(f"{API}/branches/{world['p1b1']}/subjects/{setup['phy']}/review-drafts", headers=admin, data={"title": "Draft"},
                        files={"file": ("d.csv", io.BytesIO(FLAGGED.replace(",correct", "").replace(",C,4", ",4").replace(",A,4", ",4").encode()), "text/csv"),
                               "answer_key": ("k.csv", io.BytesIO(b"number,answer\n1,C\n2,A\n"), "text/csv")})
    assert draft.status_code == 201, draft.text
    # a photo draft's page
    fid, _ = stored(db_factory, world["p1"])
    with db_factory() as db:
        pd = PhotoDraft(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=setup["phy"], title="Photos",
                        client_request_id=uuid4().hex, created_by=uuid4())
        db.add(pd)
        db.flush()
        db.add(PhotoDraftFile(draft_id=pd.draft_id, kind="PAGE", position=1, file_id=fid))
        db.commit()
        before = db.query(StoredFile).count()
    age_all(db_factory, 400)
    report = sweep(db_factory, provider_id=world["p1"], dry_run=False)
    assert report["files"] == [] and report["referenced"] == before == 6
    with db_factory() as db:
        assert db.query(StoredFile).count() == before and all((store().root / sf.storage_key).exists() for sf in db.query(StoredFile))


def test_another_providers_file_is_never_touched(world, db_factory):
    fid, key = stored(db_factory, world["p2"])
    assert sweep(db_factory, provider_id=world["p1"], dry_run=False)["files"] == [] and exists(db_factory, fid, key) == (True, True)
    assert sweep(db_factory, provider_id=world["p2"], dry_run=False)["files"][0]["outcome"] == "deleted"


def test_a_file_referenced_during_the_run_is_kept(world, setup, db_factory, monkeypatch):
    from inst_paper.services import file_sweeper
    from institute_common.models.papers import PhotoDraft, PhotoDraftFile
    fid, key = stored(db_factory, world["p1"])
    real = file_sweeper._lock

    def referenced_meanwhile(db, file_id):              # between the first check and the lock, another request starts using it
        with db_factory() as other:
            pd = PhotoDraft(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=setup["phy"], title="Photos",
                            client_request_id=uuid4().hex, created_by=uuid4())
            other.add(pd)
            other.flush()
            other.add(PhotoDraftFile(draft_id=pd.draft_id, kind="PAGE", position=1, file_id=file_id))
            other.commit()
        return real(db, file_id)
    monkeypatch.setattr(file_sweeper, "_lock", referenced_meanwhile)
    item = sweep(db_factory, provider_id=world["p1"], dry_run=False)["files"][0]
    assert item["outcome"] == "kept_now_referenced" and item["now_referenced_by"] == ["paper_photo_draft_files.file_id"]
    assert exists(db_factory, fid, key) == (True, True)


def test_missing_bytes_are_reported_not_an_error(world, db_factory):
    fid, key = stored(db_factory, world["p1"])
    store().delete(key)
    assert sweep(db_factory, provider_id=world["p1"], dry_run=False)["files"][0]["outcome"] == "bytes_already_missing"
    assert exists(db_factory, fid, key) == (False, False)


def test_a_filesystem_error_leaves_bytes_that_the_unowned_bytes_mode_recovers(world, db_factory, monkeypatch):
    from inst_paper.services.filestore import LocalFileStore
    fid, key = stored(db_factory, world["p1"])
    real = LocalFileStore.delete

    def refuse(self, k):
        raise PermissionError(k)
    monkeypatch.setattr(LocalFileStore, "delete", refuse)
    item = sweep(db_factory, provider_id=world["p1"], dry_run=False)["files"][0]
    assert item["outcome"] == "bytes_delete_failed (PermissionError)" and exists(db_factory, fid, key) == (False, True)
    monkeypatch.setattr(LocalFileStore, "delete", real)
    old_bytes(key)
    bytes_run = sweep(db_factory, "bytes", dry_run=False)
    assert [(i["storage_name"], i["outcome"]) for i in bytes_run["files"]] == [(key, "deleted")] and exists(db_factory, fid, key) == (False, False)


def test_an_interrupted_draft_cleanup_is_recovered(client, world, setup, admin, db_factory, monkeypatch):
    """A review-draft cleanup that committed and then stopped before deleting the bytes (the Stage 4.1 gap)."""
    from inst_paper.services import draft_retention
    from inst_paper.services.filestore import LocalFileStore
    from institute_common.models.papers import PaperReviewDraft, StoredFile
    draft_id = start(client, admin, world, setup).json()["draft"]["draft_id"]
    with db_factory() as db:
        d = db.get(PaperReviewDraft, UUID(draft_id))
        key = db.get(StoredFile, d.file_id).storage_key
        d.created_at = d.updated_at = d.last_opened_at = utcnow() - timedelta(days=40)
        db.commit()

    def crash(self, k):
        raise KeyboardInterrupt                               # the process stops right after the commit
    monkeypatch.setattr(LocalFileStore, "delete", crash)
    with pytest.raises(KeyboardInterrupt), service_context("paper"), db_factory() as db:
        draft_retention.cleanup(db, provider_id=world["p1"], dry_run=False)
    monkeypatch.undo()
    assert (store().root / key).exists()
    with db_factory() as db:
        assert db.get(PaperReviewDraft, UUID(draft_id)) is None and db.query(StoredFile).filter_by(storage_key=key).count() == 0
    old_bytes(key)
    first = sweep(db_factory, "bytes", dry_run=True)
    assert [(i["storage_name"], i["reason"], i["outcome"]) for i in first["files"]] == [(key, "no_record", "would_delete")]
    assert (store().root / key).exists()                      # the dry run deleted nothing
    assert sweep(db_factory, "bytes", dry_run=False)["files"][0]["outcome"] == "deleted" and not (store().root / key).exists()
    assert sweep(db_factory, "bytes", dry_run=False)["files"] == []          # repeated: nothing left


def test_the_unowned_bytes_mode_touches_only_old_unregistered_storage_files(client, world, db_factory):
    root = store().root
    fid, registered = stored(db_factory, world["p1"])          # registered: kept
    old_bytes(registered)
    young, _ = store().save(b"young")                          # just written (an upload in progress): kept
    orphan, _ = store().save(b"orphan")
    old_bytes(orphan)
    (root / "notes.txt").write_text("not ours")                # other names, a directory and a symlink: never touched
    (root / ("a" * 31 + "G")).write_text("almost")
    (root / ("b" * 32 + ".bak")).write_text("suffix")
    (root / ("c" * 32)).mkdir()
    (root / ("d" * 32)).symlink_to(root / orphan)
    for name in ("notes.txt", "a" * 31 + "G", "b" * 32 + ".bak"):
        old = time.time() - 30 * 86400
        os.utime(root / name, (old, old))
    report = sweep(db_factory, "bytes", dry_run=False)
    assert [i["storage_name"] for i in report["files"]] == [orphan]
    assert (report["registered"], report["younger"], report["ignored"]) == (1, 1, 5)
    assert all((root / n).exists() or (root / n).is_symlink() for n in (registered, young, "notes.txt", "a" * 31 + "G", "b" * 32 + ".bak", "c" * 32, "d" * 32))
    assert not (root / orphan).exists()
    assert all("orphan" not in str(d) and "page.jpg" not in str(d) for _, d in events(db_factory))   # no contents, no uploaded names


@pytest.mark.parametrize("mode", ["records", "bytes"])
def test_a_minimum_age_below_one_day_is_refused(world, db_factory, mode):
    from inst_paper.services import file_sweeper
    with pytest.raises(file_sweeper.SweepError):
        sweep(db_factory, mode, **({"provider_id": world["p1"]} if mode == "records" else {}), min_age_days=0, dry_run=False)


@pytest.mark.parametrize("argv", [[], ["--provider-id", str(uuid4()), "--unowned-bytes"]])
def test_the_command_needs_exactly_one_mode(argv):
    from inst_paper import sweep_stored_files
    with pytest.raises(SystemExit) as exc:
        sweep_stored_files.main(argv)
    assert exc.value.code == 2


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"), reason="row locks: PostgreSQL only")
def test_a_reference_being_added_in_an_open_transaction_makes_the_sweep_skip_the_file(world, setup, db_factory):
    """PostgreSQL: inserting a row that references the file takes a key-share lock on stored_files; the sweep's FOR UPDATE SKIP LOCKED
    then skips it instead of deleting a file that is about to be referenced."""
    from institute_common.models.papers import PhotoDraft, PhotoDraftFile
    fid, key = stored(db_factory, world["p1"])
    with db_factory() as pending:                            # another request, not yet committed
        pd = PhotoDraft(provider_id=world["p1"], branch_id=world["p1b1"], subject_id=setup["phy"], title="Photos",
                        client_request_id=uuid4().hex, created_by=uuid4())
        pending.add(pd)
        pending.flush()
        pending.add(PhotoDraftFile(draft_id=pd.draft_id, kind="PAGE", position=1, file_id=fid))
        pending.flush()                                      # the foreign key check has locked the stored_files row
        item = sweep(db_factory, provider_id=world["p1"], dry_run=False)["files"][0]
        assert item["outcome"] == "skipped_in_use"
        pending.commit()
    assert exists(db_factory, fid, key) == (True, True)
    assert sweep(db_factory, provider_id=world["p1"], dry_run=False)["files"] == []          # now referenced: not a candidate
