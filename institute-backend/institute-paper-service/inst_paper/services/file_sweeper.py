"""Stored files nothing uses any more (run on demand only: python -m inst_paper.sweep_stored_files). Nothing schedules it.

Two kinds of orphan, swept separately:

  RECORDS (one provider per run): a stored_files row that no record references (services/file_refs: every foreign key to
  stored_files, read from the schema - uploads of any status, photo-draft pages, review drafts ...). Today they arise when a photo of
  a photo draft is replaced (the old file is no longer reachable through any API). A paper version reaches its file only through
  its upload, so a file of an imported or published paper is always referenced and never a candidate.
    * only rows of that provider, older than the minimum age;
    * each candidate is locked (FOR UPDATE SKIP LOCKED) and its references are counted AGAIN under the lock. In PostgreSQL a row
      that starts to reference the file takes a key-share lock on it, so a reference being added either finished before (and is
      seen by the recount) or waits for this transaction and then fails its foreign key - it can never be lost silently;
    * the row is deleted and committed first, the bytes afterwards: no record ever points at missing bytes. Bytes that cannot be
      deleted (a filesystem error) stay as UNOWNED BYTES for the next mode.

  UNOWNED BYTES (the whole store, a separate explicit mode): a file in the store directory whose name is a storage key no
  stored_files row has - left by a cleanup interrupted between its commit and the byte deletion, or by an upload whose transaction
  failed after the bytes were written. Such bytes have no recorded provider, so this mode cannot be limited to one; the store
  directory belongs to the paper service alone, and stored_files is the only registry of its keys.
    * only regular files named exactly like a storage key (32 lowercase hex characters); anything else - other names,
      directories, symlinks - is never touched, only counted;
    * older than the minimum age by modification time (an upload in progress writes its bytes moments before its row);
    * the registry is asked again immediately before each deletion.

Both: dry run by default (reports, changes nothing, records nothing); safe to repeat (a second run finds nothing; bytes already
gone are reported, not an error); every deletion is audited with ids, provider, age, reason and outcome - never file contents, names
of uploaded files, review tokens or correction text."""
import re
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.database import utcnow
from institute_common.models.papers import StoredFile
from inst_paper.services import file_refs
from inst_paper.services.filestore import LocalFileStore, get_store

MIN_AGE_DAYS, MIN_ALLOWED_DAYS = 7, 1
STORAGE_NAME = re.compile(r"^[0-9a-f]{32}$")          # LocalFileStore.save: secrets.token_hex(16)


class SweepError(ValueError):
    pass


def _check_age(min_age_days: int) -> None:
    if min_age_days < MIN_ALLOWED_DAYS:
        raise SweepError(f"The minimum age must be at least {MIN_ALLOWED_DAYS} day(s).")


def _delete_bytes(store: LocalFileStore, key: str) -> str:
    try:
        return "deleted" if store.delete(key) else "bytes_already_missing"
    except OSError as exc:                             # permissions, I/O: reported; left for a later unowned-bytes run
        return f"bytes_delete_failed ({type(exc).__name__})"


def _lock(db: Session, file_id: UUID) -> StoredFile | None:
    return db.scalar(select(StoredFile).where(StoredFile.file_id == file_id).with_for_update(skip_locked=True))


def sweep_records(db: Session, *, provider_id: UUID, min_age_days: int = MIN_AGE_DAYS, now: datetime | None = None,
                  dry_run: bool = True, store: LocalFileStore | None = None) -> dict:
    _check_age(min_age_days)
    now, store = now or utcnow(), store or get_store()
    cutoff = now - timedelta(days=min_age_days)
    report = {"mode": "records", "dry_run": dry_run, "provider_id": str(provider_id), "min_age_days": min_age_days,
              "now": now.isoformat(), "files": [], "referenced": 0, "younger": 0}
    rows = db.execute(select(StoredFile.file_id, StoredFile.created_at).where(StoredFile.provider_id == provider_id)
                      .order_by(StoredFile.created_at)).all()
    for fid, created in rows:
        if created >= cutoff:
            report["younger"] += 1
            continue
        if file_refs.references(db, fid):
            report["referenced"] += 1
            continue
        item = {"file_id": str(fid), "provider_id": str(provider_id), "age_days": (now - created).days, "reason": "unreferenced"}
        report["files"].append(item)
        if dry_run:
            item["outcome"] = "would_delete"
            continue
        sf = _lock(db, fid)
        if sf is None or sf.provider_id != provider_id:    # being changed right now (or gone): never waited for, never guessed
            item["outcome"] = "skipped_in_use"
            db.rollback()
            continue
        if refs := file_refs.references(db, fid):          # the recount under the lock
            item["outcome"], item["now_referenced_by"] = "kept_now_referenced", refs
            db.rollback()
            continue
        key = sf.storage_key
        db.delete(sf)
        db.commit()
        item["outcome"] = _delete_bytes(store, key)
        audit.record(db, "paper.stored_file.sweep", "SUCCESS", target_type="stored_file", target_id=fid, provider_id=provider_id,
                     details={"file_id": str(fid), "age_days": item["age_days"], "reason": "unreferenced", "outcome": item["outcome"]})
    if dry_run:
        db.rollback()
    else:
        audit.record(db, "paper.stored_file.sweep_run", "SUCCESS", provider_id=provider_id,
                     details={"mode": "records", "files": len(report["files"]), "min_age_days": min_age_days,
                              "deleted": sum(1 for i in report["files"] if i["outcome"] == "deleted")})
    return report


def sweep_unowned_bytes(db: Session, *, min_age_days: int = MIN_AGE_DAYS, now: datetime | None = None, dry_run: bool = True,
                        store: LocalFileStore | None = None) -> dict:
    _check_age(min_age_days)
    now, store = now or utcnow(), store or get_store()
    cutoff = (now - timedelta(days=min_age_days)).timestamp()
    report = {"mode": "unowned-bytes", "dry_run": dry_run, "min_age_days": min_age_days, "now": now.isoformat(), "files": [],
              "registered": 0, "younger": 0, "ignored": 0}

    def registered(name: str) -> bool:
        return db.scalar(select(StoredFile.file_id).where(StoredFile.storage_key == name)) is not None

    entries = sorted(store.root.iterdir()) if store.root.is_dir() else []
    for entry in entries:
        if entry.is_symlink() or not entry.is_file() or not STORAGE_NAME.match(entry.name):
            report["ignored"] += 1                         # not something this store wrote: never touched
            continue
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:                          # removed meanwhile
            continue
        if mtime >= cutoff:
            report["younger"] += 1
            continue
        if registered(entry.name):
            report["registered"] += 1
            continue
        item = {"storage_name": entry.name, "age_days": int((now.timestamp() - mtime) // 86400), "reason": "no_record"}
        report["files"].append(item)
        if dry_run:
            item["outcome"] = "would_delete"
            continue
        if registered(entry.name):                         # asked again immediately before deleting
            item["outcome"] = "kept_now_registered"
            continue
        item["outcome"] = _delete_bytes(store, entry.name)
        audit.record(db, "paper.stored_file.sweep", "SUCCESS", target_type="stored_bytes", target_id=entry.name,
                     details={"storage_name": entry.name, "age_days": item["age_days"], "reason": "no_record", "outcome": item["outcome"]})
    db.rollback()
    if not dry_run:
        audit.record(db, "paper.stored_file.sweep_run", "SUCCESS",
                     details={"mode": "unowned-bytes", "files": len(report["files"]), "min_age_days": min_age_days, "ignored": report["ignored"],
                              "deleted": sum(1 for i in report["files"] if i["outcome"] == "deleted")})
    return report

