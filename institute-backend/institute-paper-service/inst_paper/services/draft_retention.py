"""Retention of import review drafts and the source files they keep (run on demand: python -m inst_paper.cleanup_review_drafts).

Policy
  * OPEN draft: abandoned when it was neither changed nor opened/resumed for `open_days` (default 30, never fewer than 7). Its
    activity is the latest of updated_at and last_opened_at, so a review someone keeps opening is never removed.
  * DISCARDED draft: removed `discarded_days` (default 7, at least 1) after it was discarded - a short window in which the record
    and its file still exist for a support question.
  * IMPORTED draft: never removed here; its file belongs to the imported upload and paper version.
  * A removed draft's stored files are deleted only when NOTHING else references them: every column of every table that points at
    stored_files (uploads - including a REJECTED upload left by an attempted import -, other drafts, photo drafts, ...) is checked,
    found from the schema rather than a list, so a future reference is respected too. Otherwise the file is kept and reported.
  * Scope: one provider (optionally one branch) per run; a file is only ever deleted when it belongs to that provider.
  * Safe to repeat: a run removes what is due and nothing else; a second run finds nothing. A draft being saved at that moment is
    skipped (row lock, SKIP LOCKED) and its age is checked again under the lock. Bytes are deleted only after the database rows are
    committed, so no row ever points at a missing file.
  * Audit: one event per removed draft and one per run - ids, reason, counts; never a review token or a decision's text.
  * dry_run (the default): reports what would be removed, changes nothing and records nothing."""
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.database import utcnow
from institute_common.models.papers import PaperReviewDraft, StoredFile
from inst_paper.services import file_refs
from inst_paper.services.filestore import LocalFileStore, get_store

OPEN_DAYS, DISCARDED_DAYS = 30, 7
MIN_OPEN_DAYS, MIN_DISCARDED_DAYS = 7, 1


class RetentionError(ValueError):
    pass


def _activity(d: PaperReviewDraft) -> datetime:
    return max(t for t in (d.updated_at, d.last_opened_at, d.created_at) if t is not None)


def _due(d: PaperReviewDraft, now: datetime, open_days: int, discarded_days: int) -> str | None:
    """Why the draft is due for removal ("abandoned" | "discarded"), or None."""
    if d.kind != "IMPORT":
        return None
    if d.status == "OPEN" and _activity(d) < now - timedelta(days=open_days):
        return "abandoned"
    if d.status == "DISCARDED" and d.updated_at < now - timedelta(days=discarded_days):
        return "discarded"
    return None


def _references(db: Session, file_id: UUID, draft_id: UUID) -> list[str]:
    """Every "table.column" still pointing at the file, other than the draft being removed (found from the schema)."""
    return file_refs.references(db, file_id, ignore=(PaperReviewDraft.__tablename__, "draft_id", draft_id))


def cleanup(db: Session, *, provider_id: UUID, branch_id: UUID | None = None, now: datetime | None = None,
            open_days: int = OPEN_DAYS, discarded_days: int = DISCARDED_DAYS, dry_run: bool = True,
            store: LocalFileStore | None = None) -> dict:
    if open_days < MIN_OPEN_DAYS or discarded_days < MIN_DISCARDED_DAYS:
        raise RetentionError(f"Retention must be at least {MIN_OPEN_DAYS} days for open and {MIN_DISCARDED_DAYS} day(s) for discarded reviews.")
    now = now or utcnow()
    store = store or get_store()
    open_cutoff, discarded_cutoff = now - timedelta(days=open_days), now - timedelta(days=discarded_days)
    q = select(PaperReviewDraft.draft_id).where(
        PaperReviewDraft.provider_id == provider_id, PaperReviewDraft.kind == "IMPORT",
        or_(PaperReviewDraft.status == "OPEN", PaperReviewDraft.status == "DISCARDED"),
        or_(PaperReviewDraft.status == "OPEN", PaperReviewDraft.updated_at < discarded_cutoff),
        or_(PaperReviewDraft.status == "DISCARDED", PaperReviewDraft.updated_at < open_cutoff))   # last_opened_at: checked per draft
    if branch_id is not None:
        q = q.where(PaperReviewDraft.branch_id == branch_id)
    report = {"dry_run": dry_run, "provider_id": str(provider_id), "branch_id": str(branch_id) if branch_id else None,
              "open_days": open_days, "discarded_days": discarded_days, "now": now.isoformat(), "drafts": [], "skipped_in_use": 0}
    for draft_id in db.scalars(q.order_by(PaperReviewDraft.created_at)).all():
        lock = select(PaperReviewDraft).where(PaperReviewDraft.draft_id == draft_id)
        d = db.scalar(lock if dry_run else lock.with_for_update(skip_locked=True))
        if d is None:                                         # being saved right now (or already removed by a parallel run)
            report["skipped_in_use"] += 1
            continue
        reason = _due(d, now, open_days, discarded_days)       # checked again: it may have been opened since the list was made
        if reason is None:
            db.rollback()
            continue
        files, kept = [], {}
        for fid in dict.fromkeys(f for f in (d.file_id, d.answer_key_file_id) if f is not None):
            sf = db.get(StoredFile, fid)
            refs = _references(db, fid, d.draft_id)
            if sf is not None and sf.provider_id != d.provider_id:
                refs.append("another provider")              # never: a draft's files are its provider's - but never crossed either
            if sf is None:
                continue
            if refs:
                kept[str(fid)] = refs
            else:
                files.append(sf)
        item = {"draft_id": str(d.draft_id), "branch_id": str(d.branch_id), "status": d.status, "reason": reason,
                "inactive_days": (now - _activity(d)).days, "files_deleted": [str(f.file_id) for f in files], "files_kept": kept}
        report["drafts"].append(item)
        if dry_run:
            continue
        keys = [f.storage_key for f in files]
        target = (d.draft_id, d.provider_id, d.branch_id)
        db.delete(d)
        db.flush()                                            # the draft first: then nothing points at its files
        for f in files:
            db.delete(f)
        db.commit()
        item["bytes_missing"] = item["bytes_failed"] = 0
        for k in keys:
            try:
                item["bytes_missing"] += not store.delete(k)  # already gone (a retried run): nothing to do
            except OSError:                                    # left on disk without a record: the stored-file sweeper removes it
                item["bytes_failed"] += 1
        audit.record(db, "paper.review_draft.cleanup", "SUCCESS", target_type="review_draft", target_id=target[0],
                     provider_id=target[1], branch_id=target[2],
                     details={"draft_id": str(target[0]), "reason": reason, "status": item["status"], "inactive_days": item["inactive_days"],
                              "files_deleted": len(files), "files_kept": len(kept), "bytes_failed": item["bytes_failed"]})
    if not dry_run:
        audit.record(db, "paper.review_draft.cleanup_run", "SUCCESS", provider_id=provider_id, branch_id=branch_id,
                     details={"removed": len(report["drafts"]), "files_deleted": sum(len(i["files_deleted"]) for i in report["drafts"]),
                              "skipped_in_use": report["skipped_in_use"], "open_days": open_days, "discarded_days": discarded_days})
    else:
        db.rollback()
    return report
