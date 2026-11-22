"""Bulk question import jobs: reading, validating and importing an uploaded file in the background (bulk import worker).

Life of a job (institute_common.models.bulk_import has the full status diagram):
  1. The API stores the uploaded file in the object store and creates the job (UPLOADED). Nothing else happens in the request.
  2. A worker claims it and READS the file row by row (PARSING): every row becomes a bulk_import_rows record; every distinct picture
     is checked and staged in the object store (staging/bulk-imports/<job>/...). Then it VALIDATES every row (PREVIEW).
  3. The uploader corrects rows / taxonomy values and confirms (IMPORT_QUEUED) - still nothing in the Question Bank.
  4. A worker IMPORTS the valid rows in small transactions (IMPORTING), then reports COMPLETED, PARTIAL or FAILED and deletes the
     staged pictures and the source file.

Reliability:
  * Claiming uses SELECT ... FOR UPDATE SKIP LOCKED, so two workers never take the same job; a lease (locked_until) renewed after every
    batch lets another worker resume a job whose worker died. After `bulk_import_max_attempts` claims of one stage that never finishes,
    a job is stopped (FAILED); a stage that finishes (the preview is ready) starts the count again, so corrections and re-checks never
    use it up.
  * Every step can be repeated safely: reading skips rows already stored; validation recomputes; importing only takes VALID rows
    without a question and commits each batch with the rows it created, so a crash repeats at most the unfinished batch.
  * A batch that fails is retried one row at a time, so one bad row becomes a FAILED row (partial success), not a failed import.
  * Pictures go to the object store BEFORE the row pointing at them is committed; cleanup removes staged copies and any final picture
    no question row points to, so no unmanaged objects are left behind.
"""
import hashlib
import logging
import os
import tempfile
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, load_principal
from institute_common.database import utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.org import Branch, Provider
from institute_common.models.bulk_import import BulkImportImage, BulkImportJob, BulkImportRow, BulkImportUpload
from institute_common.core import question_types as qtypes
from institute_common.models.papers import Question, QuestionImageAsset, QuestionTaxonomyNode, QuestionVersion, QuestionVersionAsset
from institute_common.objectstore import ObjectMissing, ObjectStore, ObjectStoreError, question_image_key

from inst_paper.services import bank_access, notify
from inst_paper.services import bulk_validation as rules
from inst_paper.services.bulk_reader import BulkFile, FileRejected, ReadRow
from inst_paper.services.image_assets import store_images
from inst_paper.services.question_images import ExtractedImage, ImageProblem, check_image

log = logging.getLogger("inst_paper.bulk_import")

WORK_STATES = {"UPLOADED": "PARSING", "VALIDATE_QUEUED": "VALIDATING", "IMPORT_QUEUED": "IMPORTING"}   # queued -> running
RUNNING = ("PARSING", "VALIDATING", "IMPORTING")
TERMINAL = ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "EXPIRED")
UNFINISHED = ("UPLOADED", "PARSING", "VALIDATE_QUEUED", "VALIDATING", "PREVIEW", "IMPORT_QUEUED", "IMPORTING")
READ_BATCH = 500
VALIDATE_BATCH = 1000
MIME_OF = {"XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
           "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class LeaseLost(Exception):
    """Another worker took this job over (this worker was presumed dead). Stop without touching the job."""


def source_key(job_id: UUID, file_format: str) -> str:
    return f"staging/bulk-imports/{job_id}/source.{file_format.lower()}"


def staging_key(job_id: UUID, sha256: str) -> str:
    return f"staging/bulk-imports/{job_id}/images/{sha256}"


# ------------------------------------------------------------------ worker lease

def claim_next(db: Session, worker: str, now=None) -> BulkImportJob | None:
    """Take the oldest job that has work to do (queued, or running with an expired lease). FOR UPDATE SKIP LOCKED keeps two workers
    from taking the same job; the status change and lease are committed before any work starts."""
    now = now or utcnow()
    job = db.scalar(select(BulkImportJob).where(or_(
        BulkImportJob.status.in_(list(WORK_STATES)),
        BulkImportJob.status.in_(RUNNING) & (BulkImportJob.locked_until < now)))
        .order_by(BulkImportJob.created_at).limit(1).with_for_update(skip_locked=True))
    if job is None:
        return None
    job.status = WORK_STATES.get(job.status, job.status)     # a resumed job stays in its running state
    job.attempts += 1
    job.worker = worker
    job.locked_until = now + timedelta(seconds=settings.bulk_import_lease_seconds)
    db.commit()
    return job


def renew_lease(db: Session, job: BulkImportJob, worker: str) -> None:
    """Commit the work done so far and extend the lease. LeaseLost when another worker has taken the job over meanwhile."""
    still_mine = db.execute(update(BulkImportJob).where(BulkImportJob.job_id == job.job_id, BulkImportJob.worker == worker)
                            .values(locked_until=utcnow() + timedelta(seconds=settings.bulk_import_lease_seconds))
                            .execution_options(synchronize_session=False)).rowcount
    if not still_mine:
        db.rollback()
        raise LeaseLost(str(job.job_id))
    db.commit()


def manage_problem(person: Principal, job: BulkImportJob) -> str | None:
    """Why `person` may not work with this import (see, correct, confirm, cancel; and, in the worker, have it processed), or None.
    The one rule for the API (routes/bulk_imports.py) and the worker, by the job's target:
      * GLOBAL (platform bank): a platform administrator holding question.manage;
      * provider-wide (PROVIDER, no branch): anyone who may bulk upload to the provider for that subject
        (bank_access.may_import_provider_wide, platform administrators included); everyone but the Provider Admin and platform
        administrators only their own uploads;
      * branch: question.manage for the provider, branch and subject; a teacher only their own uploads."""
    if job.scope == "GLOBAL":
        return None if person.is_global and person.has_perm("question.manage") else "only platform administrators may import platform questions"
    if job.branch_id is None:
        if not bank_access.may_import_provider_wide(person, job.provider_id, job.subject_id):
            return "the permission to add questions of this subject to the institute was removed"
        if person.staff_id != job.created_by and not (person.is_global or bank_access.is_provider_admin(person, job.provider_id)):
            return "only the Provider Admin may work with other people's uploads"
        return None
    if not person.can("question.manage", provider_id=job.provider_id, branch_id=job.branch_id, subject_id=job.subject_id):
        return "the permission to add questions to this branch and subject was removed"
    if person.staff_id != job.created_by and not person.can("question.manage", exclude_roles=(Role.TEACHER,), provider_id=job.provider_id,
                                                            branch_id=job.branch_id):
        return "a teacher may import only their own uploads"
    return None


def permission_problem(db: Session, job: BulkImportJob, staff_id: UUID) -> str | None:
    """Why `staff_id` may no longer read or import this job, or None when it still may. Checked again by the worker every time it
    claims the job, because permissions can change between upload, confirmation and the moment the worker runs (a job may wait in
    the queue, or be resumed after a crash). The rules are the API's own (manage_problem), plus: an ACTIVE account, and the target
    still there - the subject still in the platform catalogue (GLOBAL), or the provider (and branch) still active and the subject
    still offered."""
    staff, assignments, teaching = load_principal(db, staff_id)
    if staff is None or staff.status != "ACTIVE":
        return "the account is no longer active"
    subject = db.get(Subject, job.subject_id)
    if job.scope == "GLOBAL":
        if subject is None or subject.status != "ACTIVE":
            return "the subject is no longer in the platform catalogue"
    else:
        provider = db.get(Provider, job.provider_id)
        branch = db.get(Branch, job.branch_id) if job.branch_id else None
        if provider is None or provider.status != "ACTIVE" or (
                job.branch_id and (branch is None or branch.deleted_at is not None or branch.status != "ACTIVE")):
            return "the institute or branch is no longer active"
        offered = db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == job.provider_id,
                                                          ProviderSubject.subject_id == job.subject_id))
        if subject is None or subject.status != "ACTIVE" or offered is None:
            return "the subject is no longer offered"
    return manage_problem(Principal(staff, assignments, teaching, session_id="", csrf=""), job)   # the same rules as an API request


def _stop_if_not_permitted(db: Session, job: BulkImportJob, store: ObjectStore) -> bool:
    """Stop (FAILED, files deleted, audited) a job whose uploader - or, once confirmed, confirmer - lost the right to it. True if stopped.
    Questions an interrupted import already created stay, like any question their author added while allowed."""
    people = [("uploader", job.created_by)] + ([("confirmer", job.confirmed_by)] if job.confirmed_by else [])
    for role, staff_id in people:
        reason = permission_problem(db, job, staff_id)
        if reason:
            # the worker is the actor here; the person whose permission was missing is named in the details
            audit.record(db, "bulk_import.stopped", "DENIED", service="paper-bulk-import", target_type="bulk_import",
                         target_id=job.job_id, provider_id=job.provider_id, branch_id=job.branch_id,
                         details={"who": role, "staff_id": str(staff_id), "reason": reason, "stage": job.status})
            finish(db, job, store, "FAILED", f"The import was stopped: {reason} ({role}).")
            return True
    return False


def run_job(db: Session, job: BulkImportJob, store: ObjectStore, worker: str) -> None:
    """Do the claimed job's current stage, after checking again that the people behind it are still allowed to. Unexpected errors are
    retried by a later claim until attempts run out."""
    if job.attempts > settings.bulk_import_max_attempts:
        finish(db, job, store, "FAILED", "The import stopped after repeated failures. Upload the file again.")
        return
    if _stop_if_not_permitted(db, job, store):
        return
    try:
        if job.status == "PARSING":
            read_file(db, job, store, worker)
        if job.status == "VALIDATING":
            validate_job(db, job, worker)
        elif job.status == "IMPORTING":
            import_job(db, job, store, worker)
    except LeaseLost:
        log.warning("bulk import %s: lease lost, another worker continues it", job.job_id)
    except Exception:                                       # noqa: BLE001  (logged; the job is retried by the next claim)
        db.rollback()
        log.exception("bulk import %s failed in %s (attempt %s)", job.job_id, job.status, job.attempts)
        db.execute(update(BulkImportJob).where(BulkImportJob.job_id == job.job_id, BulkImportJob.worker == worker)
                   .values(locked_until=utcnow(), last_error="An unexpected error interrupted the job; it will be retried.")
                   .execution_options(synchronize_session=False))
        db.commit()


# ------------------------------------------------------------------ 1. reading

def read_file(db: Session, job: BulkImportJob, store: ObjectStore, worker: str) -> None:
    """Read every question row and stage every picture. Resumable: rows already stored are skipped."""
    done = set(db.scalars(select(BulkImportRow.row_no).where(BulkImportRow.job_id == job.job_id)))
    staged = set(db.scalars(select(BulkImportImage.sha256).where(BulkImportImage.job_id == job.job_id)))
    max_rows = settings.bulk_import_max_rows if job.file_format == "XLSX" else settings.bulk_import_max_docx_rows
    with tempfile.TemporaryDirectory(prefix="bulk-source-") as workdir:
        path = os.path.join(workdir, "source")
        try:
            store.download(job.source_key, path)
        except ObjectMissing:
            finish(db, job, store, "FAILED", "The uploaded file is no longer available. Upload it again.")
            return
        try:
            with BulkFile(path, job.file_format) as upload:
                checked: dict[str, str] = {}               # media part -> sha256 or a problem message (each picture checked once)
                pending = 0
                for read in upload.rows(max_rows):
                    if read.row_no in done:
                        continue
                    db.add(_row_record(db, job, read, upload, store, checked, staged))
                    pending += 1
                    if pending >= READ_BATCH:
                        renew_lease(db, job, worker)
                        pending = 0
        except FileRejected as exc:
            finish(db, job, store, "FAILED", str(exc), file_errors=[str(exc)])
            return
    job.status = "VALIDATING"
    renew_lease(db, job, worker)


def _row_record(db, job, read: ReadRow, upload: BulkFile, store: ObjectStore, checked: dict, staged: set) -> BulkImportRow:
    """One read row as a bulk_import_rows record, with its pictures checked and staged (each distinct picture once per job)."""
    problems = list(read.problems)
    images: dict[str, str] = {}
    for column, media in read.pictures.items():
        if media not in checked:
            checked[media] = _stage_picture(db, job, media, upload, store, staged)
        result = checked[media]
        if result.startswith("sha256:"):
            images[column] = result.removeprefix("sha256:")
        else:
            problems.append({"field": column, "message": result})
    return BulkImportRow(job_id=job.job_id, row_no=read.row_no, data=read.data, images=images or None, read_errors=problems or None)


def _stage_picture(db, job, media: str, upload: BulkFile, store: ObjectStore, staged: set) -> str:
    """Check one picture and stage it in the object store. "sha256:<hex>" on success, else a message for the row."""
    if not media:
        return "The picture could not be found inside the file."
    data = upload.picture_bytes(media)
    if isinstance(data, str):
        return data
    try:
        image = check_image(data, os.path.basename(media))
    except ImageProblem as exc:
        return str(exc)
    if image.sha256 not in staged:
        key = staging_key(job.job_id, image.sha256)
        store.put(key, data, image.mime)                     # the bytes first, then the record that points at them
        db.add(BulkImportImage(job_id=job.job_id, sha256=image.sha256, staging_key=key, file_name=image.name[:255],
                               mime_type=image.mime, file_size=len(data), width=image.width, height=image.height))
        staged.add(image.sha256)
    return f"sha256:{image.sha256}"


# ------------------------------------------------------------------ 2. validation

def taxonomy_context(job: BulkImportJob) -> rules.TaxonomyContext | None:
    """The provider's taxonomy (shared by all its branches), or the platform list for a GLOBAL import - where a typed name must be a
    record only at places that have records (TAXONOMY_ALIASES_PLAN A3; elsewhere it stays plain text, as before)."""
    return rules.TaxonomyContext(job.provider_id, job.subject_id, dict(job.taxonomy_mappings or {}), strict=job.scope != "GLOBAL")


def validate_job(db: Session, job: BulkImportJob, worker: str) -> None:
    """Validate every row not yet imported, in row order and in batches, then mark duplicates (inside the file: the first occurrence
    stays VALID; against the bank: the existing question wins)."""
    ctx = taxonomy_context(job)
    in_bank = rules.bank_hashes(db, job.provider_id, job.subject_id)
    describe = key_describer(db)
    first_row: dict[str, dict[str, str]] = {}
    last = 0
    while True:
        batch = db.scalars(select(BulkImportRow).where(BulkImportRow.job_id == job.job_id, BulkImportRow.row_no > last,
                                                       BulkImportRow.status != "IMPORTED")
                           .order_by(BulkImportRow.row_no).limit(VALIDATE_BATCH)).all()
        if not batch:
            break
        for row in batch:
            rules.validate_row(db, row, ctx)
            mark_duplicate(row, first_row, in_bank, describe)
        last = batch[-1].row_no
        renew_lease(db, job, worker)
    refresh_counts(db, job)
    # the stage finished: the attempt count is for a stage that keeps failing, not for the re-checks a user asks for
    job.status, job.locked_until, job.worker, job.attempts = "PREVIEW", None, None, 0
    if job.confirmed_at is None:                     # the first preview of an upload (a re-check later notifies nobody again)
        notify.queue(db, [job.created_by], kind="BULK_IMPORT_PREVIEW_READY", dedupe=f"bulk-preview:{job.job_id}",
                     provider_id=job.provider_id, title=f"Ready to review: {job.file_name}",
                     body=f"{job.valid_rows} question(s) are ready, {job.invalid_rows} need correcting and {job.duplicate_rows} are "
                          "duplicates. Open Question Bank & Papers > Bulk Upload to review and confirm the import.")
    db.commit()


KEY_CONFLICT_MESSAGE = ("The same question is already in the Question Bank with a different answer key ({key}). Check which key is "
                        "right; “Import anyway” adds this row as a separate question and leaves the existing one unchanged.")


def mark_duplicate(row: BulkImportRow, first_row: dict[str, dict[str, str]], in_bank: dict[str, dict[str, UUID]],
                   describe=None) -> None:
    """Classify a VALID row against the bank and the earlier rows of the file (decision D11): the same content with the same key is a
    DUPLICATE; the same content with a different key is a KEY_CONFLICT (INVALID, for review) unless the uploader chose "Import anyway"
    for this row (R1), which keeps the existing question untouched."""
    if row.status != "VALID":
        return
    key = rules.row_key(row.data)
    anyway = bool(row.data.get("import_anyway"))
    bank = in_bank.get(row.content_hash) or {}
    earlier = first_row.get(row.content_hash) or {}
    if key in bank:
        row.status, row.errors = "DUPLICATE", [rules.error(None, "This question is already in the Question Bank for this subject.")]
    elif key in earlier:
        row.status, row.errors = "DUPLICATE", [rules.error(None, f"Same question as row {earlier[key]}.")]
    elif bank and not anyway:
        existing = next(iter(bank.values()))
        shown = describe(existing) if describe else ""
        row.status, row.errors = "INVALID", [rules.error(None, KEY_CONFLICT_MESSAGE.format(key=shown or "see the existing question"),
                                                         code="KEY_CONFLICT", question_id=str(existing))]
    elif earlier and not anyway:
        other = next(iter(earlier.values()))
        row.status, row.errors = "INVALID", [rules.error(None, f"Same question as row {other} but with a different answer key. Correct "
                                                         "the key, or choose “Import anyway” to add both.", code="KEY_CONFLICT", row=other)]
    else:
        first_row.setdefault(row.content_hash, {}).setdefault(key, rules.row_label(row.row_no, row.data))


def key_describer(db: Session):
    """question id -> its latest key in words (for KEY_CONFLICT messages), cached for the pass."""
    cache: dict[UUID, str] = {}

    def describe(question_id: UUID) -> str:
        if question_id not in cache:
            v = db.scalars(select(QuestionVersion).where(QuestionVersion.question_id == question_id)
                           .order_by(QuestionVersion.version_no.desc()).limit(1)).first()
            qtype = (v.question_type or "MCQ_SINGLE") if v else "MCQ_SINGLE"
            cache[question_id] = qtypes.describe_key(qtype, v.correct_option if qtype == "MCQ_SINGLE" else v.answer_key) if v else ""
        return cache[question_id]
    return describe


def refresh_counts(db: Session, job: BulkImportJob) -> None:
    """Recount the job's rows by status (one grouped query)."""
    db.flush()          # the sessions do not autoflush: rows changed in this transaction must reach the database before counting
    counts = dict(db.execute(select(BulkImportRow.status, func.count()).where(BulkImportRow.job_id == job.job_id)
                             .group_by(BulkImportRow.status)).all())
    job.total_rows = sum(counts.values())
    job.valid_rows, job.invalid_rows = counts.get("VALID", 0), counts.get("INVALID", 0)
    job.duplicate_rows, job.imported_rows, job.failed_rows = counts.get("DUPLICATE", 0), counts.get("IMPORTED", 0), counts.get("FAILED", 0)


# ------------------------------------------------------------------ 3. importing

def import_job(db: Session, job: BulkImportJob, store: ObjectStore, worker: str) -> None:
    """Create bank questions for the VALID rows, batch by batch; rows found duplicate in the meantime are skipped and reported."""
    in_bank = rules.bank_hashes(db, job.provider_id, job.subject_id)       # includes questions this job already imported
    while True:
        batch = db.scalars(select(BulkImportRow).where(BulkImportRow.job_id == job.job_id, BulkImportRow.status == "VALID")
                           .order_by(BulkImportRow.row_no).limit(settings.bulk_import_batch_size)).all()
        if not batch:
            break
        try:
            for row in batch:
                _import_row(db, job, row, store, in_bank)
            renew_lease(db, job, worker)
        except LeaseLost:
            raise
        except Exception:                                  # noqa: BLE001  (isolate the bad row: retry this batch one row at a time)
            db.rollback()
            in_bank = rules.bank_hashes(db, job.provider_id, job.subject_id)
            for row_no in [r.row_no for r in batch]:
                row = db.get(BulkImportRow, (job.job_id, row_no))
                if row.status != "VALID":
                    continue
                try:
                    _import_row(db, job, row, store, in_bank)
                    db.flush()
                except Exception:                          # noqa: BLE001
                    db.rollback()
                    log.exception("bulk import %s: row %s could not be imported", job.job_id, row_no)
                    row = db.get(BulkImportRow, (job.job_id, row_no))
                    row.status = "FAILED"
                    row.errors = [rules.error(None, "This question could not be saved. Check its content, then import it again.")]
                renew_lease(db, job, worker)
    refresh_counts(db, job)
    outcome = "COMPLETED" if job.imported_rows and not (job.invalid_rows or job.duplicate_rows or job.failed_rows) else \
        "PARTIAL" if job.imported_rows else "FAILED"
    finish(db, job, store, outcome, None if job.imported_rows else "No question could be imported.")


def _import_row(db: Session, job: BulkImportJob, row: BulkImportRow, store: ObjectStore, in_bank: dict[str, dict[str, UUID]]) -> None:
    """Create the bank question of one VALID row (in the caller's transaction). A question with the same content and key that reached
    the bank since validation makes the row a DUPLICATE; one with a different key, a KEY_CONFLICT unless the row says import anyway."""
    key = rules.row_key(row.data)
    bank = in_bank.get(row.content_hash) or {}
    if key in bank:
        row.status = "DUPLICATE"
        row.errors = [rules.error(None, "This question is already in the Question Bank for this subject.")]
        return
    if bank and not row.data.get("import_anyway"):
        row.status = "INVALID"
        row.errors = [rules.error(None, KEY_CONFLICT_MESSAGE.format(key="see the existing question"), code="KEY_CONFLICT",
                                  question_id=str(next(iter(bank.values()))))]
        return
    data = rules.normalised(row.data)
    images = row.images or {}
    asset_ids = _final_pictures(db, job, images, store)
    qtype = rules.row_type(row.data)
    nodes = {n.node_id: n for n in db.scalars(select(QuestionTaxonomyNode).where(
        QuestionTaxonomyNode.node_id.in_([i for i in (row.chapter_id, row.topic_id, row.subtopic_id) if i])))}
    label = lambda node_id: nodes[node_id].name if node_id in nodes else None    # noqa: E731  (canonical names, not the typed text)
    # the job's target: GLOBAL (platform, plain-text labels), or PROVIDER - branch_id is the ORIGIN branch (NULL: provider-wide); a
    # provider question is shared with every branch of the provider, nothing is copied (bank_access)
    # a GLOBAL label that names no platform record yet (none exist at its place) keeps its typed text
    plain = rules.plain_labels(row.data) if job.scope == "GLOBAL" else {}
    labels = {"chapter": label(row.chapter_id) or plain.get("chapter"), "topic": label(row.topic_id) or plain.get("topic"),
              "subtopic": label(row.subtopic_id) or plain.get("subtopic")}
    question = Question(provider_id=job.provider_id, branch_id=job.branch_id, subject_id=job.subject_id, scope=job.scope,
                        owner_staff_id=job.confirmed_by, **labels, chapter_id=row.chapter_id, topic_id=row.topic_id,
                        subtopic_id=row.subtopic_id, difficulty=data["difficulty"], pattern="SINGLE_CORRECT",
                        question_type=qtype if qtype != qtypes.MCQ else data["question_type"])
    db.add(question)
    db.flush()
    if qtype != qtypes.MCQ:
        _add_typed_version(db, job, row, question, data, images, asset_ids, qtype)
    else:
        db.add(QuestionVersion(
        question_id=question.question_id, version_no=1, stem=data.get("question_text", ""),
        option_a=data.get("option_a_text", ""), option_b=data.get("option_b_text", ""), option_c=data.get("option_c_text", ""),
        option_d=data.get("option_d_text", ""), correct_option=data["correct_option"], topic=question.topic,
        marks_positive=None, marks_negative=None, created_by=job.confirmed_by,          # marks belong to the test (Phase A)
        stem_image_id=asset_ids.get("question_image"), option_a_image_id=asset_ids.get("option_a_image"),
        option_b_image_id=asset_ids.get("option_b_image"), option_c_image_id=asset_ids.get("option_c_image"),
        option_d_image_id=asset_ids.get("option_d_image")))
    row.status, row.question_id, row.errors = "IMPORTED", question.question_id, None
    in_bank.setdefault(row.content_hash, {}).setdefault(key, question.question_id)
    if row.data.get("import_anyway"):
        by = row.data.get("import_anyway_by")
        audit.record(db, "bulk_import.key_conflict_imported", "SUCCESS", actor=UUID(by) if by else job.confirmed_by,
                     target_type="question", target_id=question.question_id, provider_id=job.provider_id, branch_id=job.branch_id,
                     details={"job_id": str(job.job_id), "row_no": row.row_no, "existing_question_ids": [str(q) for q in bank.values()]},
                     commit=False)


def _add_typed_version(db, job, row, question, data, images, asset_ids, qtype) -> None:
    """Version 1 of an MSQ / NAT question: the canonical content and key (option pictures as asset ids, linked in
    question_version_assets); the MCQ columns hold their neutral values."""
    content, key = rules.typed_row(data, images)
    by_checksum = {images[c]: asset_ids[c] for c in images}
    for option in (content or {}).get("options", []):
        if option.get("image"):
            option["image"] = str(by_checksum[option["image"]])
    version = QuestionVersion(question_id=question.question_id, version_no=1, stem=data.get("question_text", ""), option_a="", option_b="",
                              option_c="", option_d="", correct_option=None, topic=question.topic, marks_positive=None,
                              marks_negative=None, created_by=job.confirmed_by, stem_image_id=asset_ids.get("question_image"),
                              question_type=qtype, content=content, answer_key=key, schema_version=qtypes.SCHEMA_VERSION)
    db.add(version)
    db.flush()
    for option in (content or {}).get("options", []):
        if option.get("image"):
            db.add(QuestionVersionAsset(question_version_id=version.question_version_id, role=f"option_{option['id'].lower()}",
                                        asset_id=UUID(option["image"])))


def _final_pictures(db: Session, job: BulkImportJob, images: dict[str, str], store: ObjectStore) -> dict[str, UUID]:
    """The picture assets of the job's bank - the provider's, or the platform's (provider_id NULL) for a GLOBAL import - for a row's
    staged pictures (created from the staged copies when not stored yet)."""
    if not images:
        return {}
    staged = {i.sha256: i for i in db.scalars(select(BulkImportImage).where(BulkImportImage.job_id == job.job_id,
                                                                             BulkImportImage.sha256.in_(set(images.values()))))}
    known = dict(db.execute(select(QuestionImageAsset.checksum, QuestionImageAsset.asset_id).where(
        _same_owner(QuestionImageAsset, job.provider_id), QuestionImageAsset.checksum.in_(set(images.values())))).all())
    wanted = [ExtractedImage(name=s.file_name, mime=s.mime_type, sha256=s.sha256, width=s.width, height=s.height,
                             data=store.get(s.staging_key)) for sha, s in staged.items() if sha not in known]
    by_sha = {**known, **store_images(db, job.provider_id, wanted, job.confirmed_by, None, object_store=store)}
    return {column: by_sha[sha] for column, sha in images.items()}


# ------------------------------------------------------------------ end of a job

def finish(db: Session, job: BulkImportJob, store: ObjectStore, status: str, message: str | None = None,
           file_errors: list[str] | None = None) -> None:
    """Record the job's final status, release the lease and delete its temporary files."""
    job.status, job.finished_at, job.locked_until, job.worker = status, utcnow(), None, None
    if message:
        job.last_error = message[:500]
    if file_errors:
        job.file_errors = file_errors
    refresh_counts(db, job)
    if status in ("COMPLETED", "PARTIAL", "FAILED"):
        title = {"COMPLETED": "Import finished", "PARTIAL": "Import finished with problems", "FAILED": "Import failed"}[status]
        body = (f"{job.imported_rows} question(s) were added to the Question Bank"
                + (f"; {job.total_rows - job.imported_rows} were not imported (see the import report)." if job.imported_rows < job.total_rows else ".")
                if status != "FAILED" or job.imported_rows else (job.last_error or "No question could be imported."))
        notify.queue(db, [job.created_by, job.confirmed_by], kind=f"BULK_IMPORT_{status}", dedupe=f"bulk-finished:{job.job_id}",
                     provider_id=job.provider_id, title=f"{title}: {job.file_name}", body=body)
    db.commit()
    cleanup(db, job, store)


def cleanup(db: Session, job: BulkImportJob, store: ObjectStore) -> None:
    """Delete the staged pictures and the source file of a finished job, and any final picture object that no asset row records
    (a picture put just before its import transaction failed). Safe to repeat; a store failure leaves cleaned_at empty so the next
    worker pass tries again."""
    if job.cleaned_at is not None:
        return
    # another import of the same provider may be putting the same picture right now: leave final objects alone then
    busy = db.scalar(select(func.count()).select_from(BulkImportJob).where(
        _same_owner(BulkImportJob, job.provider_id), BulkImportJob.status == "IMPORTING", BulkImportJob.job_id != job.job_id))
    try:
        for image in db.scalars(select(BulkImportImage).where(BulkImportImage.job_id == job.job_id)):
            store.delete(image.staging_key)
            final = question_image_key(job.provider_id, image.sha256)
            recorded = db.scalar(select(QuestionImageAsset.asset_id).where(_same_owner(QuestionImageAsset, job.provider_id),
                                                                           QuestionImageAsset.checksum == image.sha256))
            if not recorded and not busy:
                store.delete(final)                       # never recorded: an object nothing points to
        if job.source_key:
            store.delete(job.source_key)
    except ObjectStoreError:
        log.warning("bulk import %s: cleanup postponed, the object store is unavailable", job.job_id)
        return
    db.execute(update(BulkImportJob).where(BulkImportJob.job_id == job.job_id).values(cleaned_at=utcnow())
               .execution_options(synchronize_session=False))
    db.commit()


def housekeeping(db: Session, store: ObjectStore, now=None) -> dict:
    """Expire previews nobody confirmed within the retention period, and finish cleanups a store outage postponed."""
    now = now or utcnow()
    cutoff = now - timedelta(days=settings.bulk_import_preview_days)
    expired = 0
    for job in db.scalars(select(BulkImportJob).where(BulkImportJob.status.in_(("PREVIEW", "UPLOADED", "VALIDATE_QUEUED")),
                                                      BulkImportJob.updated_at < cutoff).limit(100)):
        finish(db, job, store, "EXPIRED", "The preview was not confirmed in time; its files were deleted.")
        expired += 1
    retried = 0
    for job in db.scalars(select(BulkImportJob).where(BulkImportJob.status.in_(TERMINAL), BulkImportJob.cleaned_at.is_(None)).limit(100)):
        cleanup(db, job, store)
        retried += 1
    abandoned = 0
    resume_cutoff = now - timedelta(days=settings.bulk_upload_resume_days)
    for upload in db.scalars(select(BulkImportUpload).where(BulkImportUpload.status == "RECEIVING",
                                                            BulkImportUpload.updated_at < resume_cutoff).limit(100)):
        upload.status, upload.updated_at = "DISCARDED", now
        db.commit()
        delete_upload_parts(upload, store)
        abandoned += 1
    return {"expired": expired, "cleanup_retried": retried, "uploads_discarded": abandoned}


def upload_part_key(upload_id: UUID, n: int) -> str:
    """Where piece n of a resumable upload is kept until the upload completes."""
    return f"staging/bulk-uploads/{upload_id}/{n:06d}"


def delete_upload_parts(upload: BulkImportUpload, store: ObjectStore) -> None:
    """Delete a resumable upload's pieces (best effort: deleting twice is safe; a store outage leaves them for the next pass)."""
    for n in range(max(upload.received_parts, 0)):
        try:
            store.delete(upload_part_key(upload.upload_id, n))
        except ObjectStoreError:
            return


def sha256_of_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_jobs(db: Session, provider_id: UUID | None) -> int:
    """Unfinished imports of one provider, or of the platform bank (None)."""
    return db.scalar(select(func.count()).select_from(BulkImportJob).where(_same_owner(BulkImportJob, provider_id),
                                                                           BulkImportJob.status.in_(UNFINISHED))) or 0


def _same_owner(model, provider_id: UUID | None):
    """Rows of one provider, or the platform's (provider_id NULL: GLOBAL jobs and platform pictures)."""
    return model.provider_id == provider_id if provider_id else model.provider_id.is_(None)

