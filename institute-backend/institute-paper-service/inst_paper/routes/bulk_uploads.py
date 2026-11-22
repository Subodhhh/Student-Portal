"""Resumable bulk uploads: a large question file is sent in pieces, so a transfer that is cut off (tab closed, network lost, laptop
asleep) continues from where it stopped when the same file is chosen again, instead of starting over.

    POST   /bulk-imports/uploads                      start - or resume the open upload of the same file, target and subject
    GET    /bulk-imports/uploads                      the caller's unfinished uploads (to continue them)
    GET    /bulk-imports/uploads/{id}                 one upload: how much has arrived
    PUT    /bulk-imports/uploads/{id}/parts/{n}       piece n (0-based, `chunk_size` bytes; the last one the remainder)
    POST   /bulk-imports/uploads/{id}/complete        every byte is in: the pieces become the job's file and the job is queued
    DELETE /bulk-imports/uploads/{id}                 stop and delete what was sent

An upload belongs to the person who started it (anyone else gets 404). Who may import where is decided exactly as for a one-request
upload (routes/bulk_imports._target), at the start and again at completion; the worker checks it once more before reading the file.
Pieces live in the object store (staging/bulk-uploads/<upload>/<n>), never in memory beyond one piece, and are deleted when the upload
completes, is discarded, or is abandoned for BULK_UPLOAD_RESUME_DAYS (services/bulk_import.housekeeping).
"""
import os
import tempfile
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.bulk_import import BulkImportUpload
from institute_common.objectstore import ObjectMissing, ObjectStoreError, get_object_store
from institute_common.util import Strict

from inst_paper.routes import bulk_imports as imports
from inst_paper.services import bulk_import as jobs

router = APIRouter(prefix="/api/v1", tags=["Bulk question import"])


part_key = jobs.upload_part_key


def parts_of(upload: BulkImportUpload) -> int:
    return -(-upload.size // upload.chunk_size)          # pieces in the whole file (ceiling division)


class StartIn(Strict):
    subject_id: UUID
    target: Literal["GLOBAL", "PROVIDER"]
    provider_id: UUID | None = None
    file_name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    fingerprint: str = Field(min_length=1, max_length=300)     # the browser's name|size|last-modified of the file


class UploadOut(BaseModel):
    upload_id: UUID
    scope: str
    provider_id: UUID | None
    subject_id: UUID
    file_name: str
    size: int
    chunk_size: int
    received_parts: int
    received_bytes: int
    status: str
    job_id: UUID | None
    created_at: datetime
    updated_at: datetime
    resumed: bool = False


def _out(u: BulkImportUpload, resumed: bool = False) -> UploadOut:
    return UploadOut(**{c: getattr(u, c) for c in UploadOut.model_fields if c != "resumed"}, resumed=resumed)


def _mine(db: Session, p: Principal, upload_id: UUID, *, lock: bool = False) -> BulkImportUpload:
    q = select(BulkImportUpload).where(BulkImportUpload.upload_id == upload_id)
    u = db.scalar(q.with_for_update() if lock else q)
    if u is None or u.created_by != p.staff_id:
        raise api_error(404, "NOT_FOUND", "Upload not found.")        # another person's upload does not exist for the caller
    return u


def _receiving(u: BulkImportUpload) -> None:
    if u.status != "RECEIVING":
        raise api_error(409, "UPLOAD_CLOSED", "This upload was already completed or stopped. Choose the file again to start over.")


@router.post("/bulk-imports/uploads", response_model=UploadOut, status_code=201)
def start(body: StartIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Start an upload, or - for the same file (fingerprint), target and subject - return the caller's open one to continue."""
    scope, provider_id, _branch = imports._target(db, p, body.target, body.provider_id, None, body.subject_id)
    name = os.path.basename(body.file_name)[:255] or "upload"
    file_format = imports.FORMATS.get(os.path.splitext(name.lower())[1])
    if file_format is None:
        raise api_error(422, "UNSUPPORTED_FORMAT", "Upload the Excel (.xlsx) or Word (.docx) template. CSV and .doc are not supported.")
    if body.size > settings.bulk_import_max_bytes:
        limit = settings.bulk_import_max_bytes
        shown = f"{limit / 1024 ** 3:g} GB" if limit >= 1024 ** 3 else f"{limit // 1024 ** 2} MB"
        raise api_error(413, "FILE_TOO_LARGE", f"The file is larger than {shown}.")
    open_one = db.scalar(select(BulkImportUpload).where(
        BulkImportUpload.created_by == p.staff_id, BulkImportUpload.status == "RECEIVING", BulkImportUpload.fingerprint == body.fingerprint,
        BulkImportUpload.size == body.size, BulkImportUpload.scope == scope, BulkImportUpload.subject_id == body.subject_id,
        BulkImportUpload.provider_id.is_(None) if provider_id is None else BulkImportUpload.provider_id == provider_id))
    if open_one is not None:
        return _out(open_one, resumed=True)
    if jobs.active_jobs(db, provider_id) >= settings.bulk_import_max_active_jobs:
        raise api_error(429, "TOO_MANY_IMPORTS", "Finish or cancel an import in progress before starting another.")
    u = BulkImportUpload(scope=scope, provider_id=provider_id, subject_id=body.subject_id, file_format=file_format, file_name=name,
                         fingerprint=body.fingerprint, size=body.size, chunk_size=settings.bulk_upload_chunk_bytes, created_by=p.staff_id)
    db.add(u)
    db.commit()
    return _out(u)


@router.get("/bulk-imports/uploads", response_model=list[UploadOut])
def unfinished(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The caller's uploads still waiting for pieces, newest first: choosing the same file again continues one."""
    rows = db.scalars(select(BulkImportUpload).where(BulkImportUpload.created_by == p.staff_id, BulkImportUpload.status == "RECEIVING")
                      .order_by(BulkImportUpload.updated_at.desc()).limit(20))
    return [_out(u) for u in rows]


@router.get("/bulk-imports/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _out(_mine(db, p, upload_id))


@router.put("/bulk-imports/uploads/{upload_id}/parts/{n}", response_model=UploadOut)
def put_part(upload_id: UUID, n: int, data: bytes = Body(..., media_type="application/octet-stream"),
             p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Piece n, in order. A piece that already arrived is accepted again without change (a retry after a lost answer); a piece
    beyond the next one is refused with how far the upload got, so the browser continues from there."""
    u = _mine(db, p, upload_id)
    _receiving(u)
    if n < u.received_parts:
        return _out(u)                                    # already stored: idempotent
    if n > u.received_parts or n >= parts_of(u):
        raise api_error(409, "OUT_OF_ORDER", f"Send piece {u.received_parts} next.")
    expected = min(u.chunk_size, u.size - u.received_bytes)
    if len(data) != expected:
        raise api_error(422, "BAD_PIECE", f"Piece {n} must be exactly {expected} bytes.")
    if n == 0 and data[:4] != imports.ZIP_MAGIC:          # the extension is not trusted: a real .xlsx/.docx is a zip package
        raise api_error(422, "INVALID_FILE", "The file is not a valid Excel (.xlsx) or Word (.docx) file.")
    try:
        get_object_store().put(part_key(u.upload_id, n), data, "application/octet-stream")
    except ObjectStoreError:
        raise api_error(503, "STORE_UNAVAILABLE", "The file could not be stored right now. It will continue when you try again.")
    db.execute(update(BulkImportUpload).where(BulkImportUpload.upload_id == u.upload_id, BulkImportUpload.received_parts == n)
               .values(received_parts=n + 1, received_bytes=BulkImportUpload.received_bytes + len(data), updated_at=utcnow())
               .execution_options(synchronize_session=False))
    db.commit()
    db.refresh(u)
    return _out(u)


@router.post("/bulk-imports/uploads/{upload_id}/complete", response_model=imports.JobOut, status_code=202)
def complete(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Every byte arrived: the pieces become the job's file and the job is queued, exactly like a one-request upload. Completing
    twice returns the same job."""
    u = _mine(db, p, upload_id, lock=True)
    if u.status == "COMPLETED" and u.job_id:
        return imports._out(db.get(imports.BulkImportJob, u.job_id))
    _receiving(u)
    if u.received_bytes != u.size:
        raise api_error(409, "INCOMPLETE", f"{u.received_bytes:,} of {u.size:,} bytes have arrived; send the rest first.")
    scope, provider_id, branch_id = imports._target(db, p, u.scope, u.provider_id, None, u.subject_id)   # the permission, again
    if jobs.active_jobs(db, provider_id) >= settings.bulk_import_max_active_jobs:
        raise api_error(429, "TOO_MANY_IMPORTS", "Finish or cancel an import in progress before starting another.")
    store = get_object_store()
    with tempfile.TemporaryDirectory(prefix="bulk-upload-") as workdir:
        path = os.path.join(workdir, "upload")
        try:
            with open(path, "wb") as out:
                for n in range(parts_of(u)):
                    out.write(store.get(part_key(u.upload_id, n)))        # one piece in memory at a time
        except (ObjectMissing, ObjectStoreError):
            raise api_error(503, "STORE_UNAVAILABLE", "The uploaded pieces could not be read right now. Try again shortly.")
        if os.path.getsize(path) != u.size:
            raise api_error(409, "INCOMPLETE", "The uploaded pieces do not add up to the file. Choose the file again to start over.")
        job = imports.new_job(db, p, scope, provider_id, branch_id, u.subject_id, u.file_name, u.file_format, path, u.size)
    u.status, u.job_id, u.updated_at = "COMPLETED", job.job_id, utcnow()
    db.commit()
    jobs.delete_upload_parts(u, get_object_store())
    return imports._out(job)


@router.delete("/bulk-imports/uploads/{upload_id}", response_model=UploadOut)
def discard(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = _mine(db, p, upload_id, lock=True)
    _receiving(u)
    u.status, u.updated_at = "DISCARDED", utcnow()
    db.commit()
    jobs.delete_upload_parts(u, get_object_store())
    return _out(u)
