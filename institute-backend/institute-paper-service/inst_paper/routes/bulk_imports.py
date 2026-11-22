"""Bulk question import API (paper service). The heavy work happens in the bulk import worker; these endpoints upload, show,
correct, confirm and report.

Three targets (docs/institute-portal/THREE_SCOPE_QUESTION_BANK_PLAN.md), one workflow:
  * GLOBAL - the platform Question Bank: platform administrators (Super Admin, Admin) only; any active subject; plain-text labels;
  * PROVIDER (provider-wide) - the provider's central bank, no branch: the provider's own Provider Admin only;
  * BRANCH - questions originating in one branch: `question.manage` for the provider, branch and subject (unchanged).
Provider questions, whatever their origin, are shared with every branch of the provider. The rule for each job is
services/bulk_import.manage_problem (the worker checks it again at every claim). Teachers see only the imports they uploaded.
Mapping an unknown Chapter/Topic/Subtopic spelling to a record for one import is open to whoever manages the job; approving it as an
alias for good or creating a record needs `taxonomy.manage` for the list (the Provider Admin; platform administrators for the
platform list a GLOBAL import uses) - anyone else's "remember this spelling" becomes an alias request. A job the caller may not see
answers 404, exactly like a job that does not exist.

    GET    /bulk-imports/targets                         what the caller may import into (the page shows only these)
    POST   /bulk-imports                                 upload (multipart: target, provider_id | branch_id, subject_id, file)
    GET    /bulk-imports?target=GLOBAL | ?provider_id=... | ?branch_id=...   import history
    GET    /bulk-imports/{id}                            status, counts and progress
    GET    /bulk-imports/{id}/rows                       the preview, page by page (?status=INVALID ...)
    PATCH  /bulk-imports/{id}/rows/{row_no}              correct a row's text; it is validated again at once
    POST   /bulk-imports/{id}/rows/{row_no}/import-anyway a KEY_CONFLICT row becomes a separate question (R1, audited)
    GET    /bulk-imports/{id}/images/{sha256}            a picture of the preview
    GET    /bulk-imports/{id}/taxonomy-issues            unmatched chapters/topics/subtopics with suggestions
    POST   /bulk-imports/{id}/taxonomy-resolutions       map / approve an alias / create a record, then validate again
    POST   /bulk-imports/{id}/revalidate                 validate every row again
    POST   /bulk-imports/{id}/confirm                    import the valid rows (once, however often it is clicked)
    POST   /bulk-imports/{id}/cancel                     discard the preview and its files
    GET    /bulk-imports/{id}/report.csv                 every row that was not imported, with the reasons
"""
import csv
import io
import os
import tempfile
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.bulk_import import ROW_STATUSES, BulkImportImage, BulkImportJob, BulkImportRow
from institute_common.models.org import Branch, Provider
from institute_common.models.papers import QuestionTaxonomyNode
from institute_common.objectstore import ObjectMissing, ObjectStoreError, get_object_store
from institute_common.util import Strict, paginate

from inst_paper.services import bulk_import as jobs
from inst_paper.services import bulk_validation as rules
from inst_paper.routes import taxonomy as taxonomy_routes
from inst_paper.services import bank_access, taxonomy
from inst_paper.services.taxonomy import TaxonomyError

router = APIRouter(prefix="/api/v1", tags=["Bulk question import"])

ZIP_MAGIC = b"PK\x03\x04"
FORMATS = {".xlsx": "XLSX", ".docx": "DOCX"}
EDITABLE = ("PREVIEW",)
PREVIEW_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
                   "Cache-Control": "private, no-store"}


# ------------------------------------------------------------------ shapes

class JobOut(BaseModel):
    job_id: UUID
    scope: str                         # GLOBAL | PROVIDER
    provider_id: UUID | None           # None: a GLOBAL import
    branch_id: UUID | None             # None: GLOBAL or provider-wide
    subject_id: UUID
    status: str
    file_format: str
    file_name: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    imported_rows: int
    failed_rows: int
    progress: dict                     # {"done": n, "total": m} of the current stage
    file_errors: list | None
    last_error: str | None
    created_by: UUID
    created_at: datetime
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    finished_at: datetime | None


class RowPatch(Strict):
    """Corrected text of a row; only the fields given change. Pictures are changed by uploading the file again."""
    chapter: str | None = Field(default=None, max_length=300)
    topic: str | None = Field(default=None, max_length=300)
    subtopic: str | None = Field(default=None, max_length=300)
    difficulty: str | None = Field(default=None, max_length=50)
    question_type: str | None = Field(default=None, max_length=50)
    question_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    option_a_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    option_b_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    option_c_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    option_d_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    correct_option: str | None = Field(default=None, max_length=50)
    # correct_option is the Correct Answer of every type (MCQ letter, MSQ letters, NAT number); MSQ options E-H and the NAT columns
    option_e_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    option_f_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    option_g_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    option_h_text: str | None = Field(default=None, max_length=rules.MAX_TEXT)
    tolerance_type: str | None = Field(default=None, max_length=30)
    tolerance: str | None = Field(default=None, max_length=50)
    minimum: str | None = Field(default=None, max_length=50)
    maximum: str | None = Field(default=None, max_length=50)
    answer_format: str | None = Field(default=None, max_length=30)
    decimal_places: str | None = Field(default=None, max_length=5)
    unit: str | None = Field(default=None, max_length=30)


class ResolutionIn(Strict):
    """What to do with one unmatched value (level + value + parent identify it, as listed by /taxonomy-issues).
    MAP: use an existing record for this import (with `remember`: also remember the spelling - an alias at once for the list's
    administrators, an alias request for everyone else). ALIAS: approve the value as an alias of a record (every later import too).
    CREATE: create a new record named exactly like the value. ALIAS and CREATE need taxonomy.manage."""
    level: Literal["CHAPTER", "TOPIC", "SUBTOPIC"]
    value: str = Field(min_length=1, max_length=300)
    parent_id: UUID | None = None
    action: Literal["MAP", "ALIAS", "CREATE"]
    node_id: UUID | None = None
    remember: bool = False


# ------------------------------------------------------------------ helpers

def _stored_ip(p: Principal) -> str | None:
    return p.ip if p.ip != "unknown" else None


def _may_manage(p: Principal, job: BulkImportJob) -> bool:
    return jobs.manage_problem(p, job) is None          # the worker's own rule (checked again at every claim)


def _job(db: Session, p: Principal, job_id: UUID, *, lock: bool = False) -> BulkImportJob:
    stmt = select(BulkImportJob).where(BulkImportJob.job_id == job_id)
    job = db.scalar(stmt.with_for_update() if lock else stmt)
    if job is None or not _may_manage(p, job):
        if job is not None:
            audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=job.provider_id, branch_id=job.branch_id,
                         details={"permission": "question.manage", "bulk_import_id": str(job_id)})
        raise api_error(404, "NOT_FOUND", "Import not found.")
    return job


def _out(job: BulkImportJob) -> JobOut:
    done = job.imported_rows + job.failed_rows if job.status in ("IMPORT_QUEUED", "IMPORTING") or job.confirmed_at else job.total_rows
    total = (job.imported_rows + job.failed_rows + job.valid_rows) if job.confirmed_at else job.total_rows
    return JobOut(**{c: getattr(job, c) for c in JobOut.model_fields if c != "progress"}, progress={"done": done, "total": total})


def _audit(db: Session, p: Principal, action: str, job: BulkImportJob, **details) -> None:
    audit.record(db, action, "SUCCESS", actor=p.staff_id, target_type="bulk_import", target_id=job.job_id, provider_id=job.provider_id,
                 branch_id=job.branch_id, ip=p.ip, details={"file_name": job.file_name, **details})


def _save_upload(file: UploadFile, workdir: str) -> tuple[str, int]:
    """Copy the upload to a private temporary file in chunks, stopping at the size limit. (path, size)."""
    path = os.path.join(workdir, "upload")
    size = 0
    with open(path, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.bulk_import_max_bytes:
                limit = settings.bulk_import_max_bytes
                shown = f"{limit / 1024 ** 3:g} GB" if limit >= 1024 ** 3 else f"{limit // 1024 ** 2} MB"
                raise api_error(413, "FILE_TOO_LARGE", f"The file is larger than {shown}.")
            out.write(chunk)
    with open(path, "rb") as fh:
        if fh.read(4) != ZIP_MAGIC:          # the extension is not trusted: a real .xlsx/.docx is a zip package
            raise api_error(422, "INVALID_FILE", "The file is not a valid Excel (.xlsx) or Word (.docx) file.")
    return path, size


# ------------------------------------------------------------------ templates

TEMPLATES = {   # kind -> (file inside inst_paper/import_templates, download name, media type)
    "xlsx": ("question_import_template.xlsx", "question-import-template.xlsx", jobs.MIME_OF["XLSX"]),
    "docx": ("question_import_template.docx", "question-import-template.docx", jobs.MIME_OF["DOCX"]),
}
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "import_templates")


@router.get("/bulk-import-templates/{kind}")
def download_template(kind: Literal["xlsx", "docx"], p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The Excel or Word question template (generated by inst_paper/import_templates/build.py), for staff who may add questions.
    `kind` is a fixed choice mapped to a fixed file, so no user input ever reaches a file path."""
    if not p.has_perm("question.manage"):
        p.require(db, "question.manage")                  # records the denial and answers 403
    file_name, download_name, media_type = TEMPLATES[kind]
    return FileResponse(os.path.join(TEMPLATE_DIR, file_name), media_type=media_type, filename=download_name,
                        headers={"Cache-Control": "private, max-age=3600"})


# ------------------------------------------------------------------ upload and history

def _active_subjects(db: Session, provider_id: UUID | None) -> list[dict]:
    """Subjects one may import into: the platform's active subjects, or those a provider offers."""
    stmt = select(Subject.subject_id, Subject.name).where(Subject.status == "ACTIVE")
    if provider_id:
        stmt = stmt.join(ProviderSubject, ProviderSubject.subject_id == Subject.subject_id).where(ProviderSubject.provider_id == provider_id)
    return [{"subject_id": str(s), "name": n} for s, n in db.execute(stmt.order_by(Subject.name))]


@router.get("/bulk-imports/targets")
def targets(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """What the caller may import into, decided here (the page offers only these):
    `global` (platform administrators: the active subjects) and `provider_wide` (each provider the caller may bulk upload to - for
    platform administrators every active provider - with the subjects they may upload: bank_access.import_subjects). `branch` stays
    true for older clients; the page no longer offers it."""
    if not p.has_perm("question.manage"):
        p.require(db, "question.manage")
    platform = p.is_global and p.has_perm("question.manage")
    mine = set(db.scalars(select(Provider.provider_id).where(Provider.status == "ACTIVE"))) if platform else \
        {a.provider_id for a in p.assignments if a.provider_id}
    allowed = {pid: bank_access.import_subjects(p, pid) for pid in mine}
    wide = sorted((pid for pid, subjects in allowed.items() if subjects is None or subjects), key=str)
    providers = {pid: name for pid, name in db.execute(select(Provider.provider_id, Provider.name).where(
        Provider.provider_id.in_(wide), Provider.status == "ACTIVE"))} if wide else {}
    return {"global": {"subjects": _active_subjects(db, None)} if platform else None,
            "provider_wide": [{"provider_id": str(pid), "name": providers[pid],
                               "subjects": [s for s in _active_subjects(db, pid) if allowed[pid] is None or UUID(s["subject_id"]) in allowed[pid]]}
                              for pid in sorted((x for x in wide if x in providers), key=lambda x: providers[x].lower())],
            "branch": True}


def _target(db: Session, p: Principal, target: str | None, provider_id: UUID | None, branch_id: UUID | None,
            subject_id: UUID) -> tuple[str, UUID | None, UUID | None]:
    """(scope, provider_id, branch_id) of a new import, after checking the caller may import there. The provider of a branch import
    comes from the branch; of a provider-wide import from the caller's own Provider Admin assignment - never trusted from the form."""
    target = target or ("BRANCH" if branch_id else None)
    subject = db.get(Subject, subject_id)
    if target == "GLOBAL":
        if provider_id or branch_id:
            raise api_error(422, "INVALID_TARGET", "A platform import has no provider or branch.")
        if not (p.is_global and p.has_perm("question.manage")):
            bank_access.deny(db, p, "question.manage", None, reason="global_import_needs_platform_admin")
        if subject is None or subject.status != "ACTIVE":
            raise api_error(422, "SUBJECT_NOT_AVAILABLE", "Choose an active subject of the platform catalogue.")
        return "GLOBAL", None, None
    if target == "PROVIDER":
        if branch_id:
            raise api_error(422, "INVALID_TARGET", "A provider-wide import has no branch.")
        provider_id = provider_id or bank_access.single_provider(p, "question.manage")
        if provider_id is None:
            raise api_error(422, "PROVIDER_REQUIRED", "Choose the provider.")
        if not bank_access.may_import_provider_wide(p, provider_id, subject_id):
            bank_access.deny(db, p, "question.manage", provider_id, reason="provider_wide_import_not_allowed")
        provider = db.get(Provider, provider_id)
        if provider is None or provider.status != "ACTIVE":
            raise api_error(404, "NOT_FOUND", "Provider not found.")
        branch = None
    elif target == "BRANCH":
        if branch_id is None:
            raise api_error(422, "BRANCH_REQUIRED", "Choose the branch.")
        branch = db.get(Branch, branch_id)
        if branch is None or branch.deleted_at is not None or (provider_id and provider_id != branch.provider_id):
            raise api_error(404, "NOT_FOUND", "Branch not found.")
        provider_id = branch.provider_id
        p.require(db, "question.manage", provider_id=provider_id, branch_id=branch_id, subject_id=subject_id)
    else:
        raise api_error(422, "INVALID_TARGET", "Choose where to import: GLOBAL, PROVIDER or BRANCH.")
    offered = db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id, ProviderSubject.subject_id == subject_id))
    if subject is None or subject.status != "ACTIVE" or offered is None:
        raise api_error(422, "SUBJECT_NOT_ENABLED", "Choose a subject this institute offers.")
    return "PROVIDER", provider_id, (branch.branch_id if branch else None)


@router.post("/bulk-imports", response_model=JobOut, status_code=202)
def upload(subject_id: UUID = Form(...), file: UploadFile = File(...), target: Literal["GLOBAL", "PROVIDER", "BRANCH"] | None = Form(None),
           provider_id: UUID | None = Form(None), branch_id: UUID | None = Form(None),
           p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Store the file and queue it for reading. Nothing is read or imported in this request. Without `target`, a `branch_id` means a
    branch import (the original form of this call)."""
    scope, provider_id, branch_id = _target(db, p, target, provider_id, branch_id, subject_id)
    name = os.path.basename(file.filename or "")[:255] or "upload"
    file_format = FORMATS.get(os.path.splitext(name.lower())[1])
    if file_format is None:
        raise api_error(422, "UNSUPPORTED_FORMAT", "Upload the Excel (.xlsx) or Word (.docx) template. CSV and .doc are not supported.")
    if jobs.active_jobs(db, provider_id) >= settings.bulk_import_max_active_jobs:
        raise api_error(429, "TOO_MANY_IMPORTS", "Finish or cancel an import in progress before starting another.")
    with tempfile.TemporaryDirectory(prefix="bulk-upload-") as workdir:
        path, size = _save_upload(file, workdir)
        job = new_job(db, p, scope, provider_id, branch_id, subject_id, name, file_format, path, size)
    return _out(job)


def new_job(db: Session, p: Principal, scope: str, provider_id: UUID | None, branch_id: UUID | None, subject_id: UUID, name: str,
            file_format: str, path: str, size: int) -> BulkImportJob:
    """Store a received file (on disk at `path`) as a new job's source and queue the job for reading: the end of a one-request upload
    and of a resumable one alike."""
    job_id = uuid4()
    key = jobs.source_key(job_id, file_format)
    try:
        get_object_store().put_file(key, path, jobs.MIME_OF[file_format])
    except ObjectStoreError:
        raise api_error(503, "STORE_UNAVAILABLE", "The file could not be stored right now. Try again shortly.")
    job = BulkImportJob(job_id=job_id, scope=scope, provider_id=provider_id, branch_id=branch_id, subject_id=subject_id, file_format=file_format,
                        file_name=name, source_key=key, source_size=size, source_sha256=jobs.sha256_of_file(path),
                        created_by=p.staff_id, created_ip=_stored_ip(p))
    db.add(job)
    db.commit()
    _audit(db, p, "bulk_import.upload", job, size=size, format=file_format)
    return job


@router.get("/bulk-imports")
def history(branch_id: UUID | None = None, provider_id: UUID | None = None, target: Literal["GLOBAL"] | None = None,
            page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
            p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Imports, newest first: a branch's (teachers see their own), a provider's provider-wide imports (?provider_id=: the Provider
    Admin all of them, everyone else who may upload there their own), or the platform's (?target=GLOBAL, platform administrators)."""
    if target == "GLOBAL":
        if not (p.is_global and p.has_perm("question.manage")):
            bank_access.deny(db, p, "question.manage", None, reason="global_import_history")
        stmt = select(BulkImportJob).where(BulkImportJob.scope == "GLOBAL")
    elif branch_id:
        br = db.get(Branch, branch_id)
        if br is None:
            raise api_error(404, "NOT_FOUND", "Branch not found.")
        p.require(db, "question.manage", provider_id=br.provider_id, branch_id=branch_id)
        stmt = select(BulkImportJob).where(BulkImportJob.branch_id == branch_id)
        if not p.can("question.manage", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
            stmt = stmt.where(BulkImportJob.created_by == p.staff_id)
    elif provider_id:
        if bank_access.import_subjects(p, provider_id) == set():
            bank_access.deny(db, p, "question.manage", provider_id, reason="provider_wide_import_history")
        stmt = select(BulkImportJob).where(BulkImportJob.scope == "PROVIDER", BulkImportJob.provider_id == provider_id,
                                           BulkImportJob.branch_id.is_(None))
    else:
        raise api_error(422, "BRANCH_REQUIRED", "Choose the branch, the provider or the platform.")
    res = paginate(db, stmt.order_by(BulkImportJob.created_at.desc()), page, page_size)
    res["items"] = [_out(j) for j in res["items"] if _may_manage(p, j)]
    return res


@router.get("/bulk-imports/{job_id}", response_model=JobOut)
def get_job(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _out(_job(db, p, job_id))


# ------------------------------------------------------------------ preview and corrections

IMAGE_ATTR = {"question_image": "stem_image_id", "option_a_image": "option_a_image_id", "option_b_image": "option_b_image_id",
              "option_c_image": "option_c_image_id", "option_d_image": "option_d_image_id"}


def _stored_pictures(db: Session, job: BulkImportJob, rows: list[BulkImportRow]) -> dict:
    """{question_id: {column: asset_id}} of the imported rows once the staged copies are gone (the history then shows the pictures
    the questions really have, through the authorised /question-images endpoint)."""
    from institute_common.models.papers import QuestionVersion, QuestionVersionAsset
    ids = [r.question_id for r in rows if r.question_id and r.images]
    if job.cleaned_at is None or not ids:
        return {}
    versions = list(db.scalars(select(QuestionVersion).where(QuestionVersion.question_id.in_(ids), QuestionVersion.version_no == 1)))
    out = {v.question_id: {c: getattr(v, a) for c, a in IMAGE_ATTR.items() if getattr(v, a)} for v in versions}
    question_of = {v.question_version_id: v.question_id for v in versions}
    for link in db.scalars(select(QuestionVersionAsset).where(QuestionVersionAsset.question_version_id.in_(list(question_of)))):
        out[question_of[link.question_version_id]][f"{link.role}_image"] = link.asset_id   # MSQ option pictures (role option_a ..)
    return out


def _row_out(job: BulkImportJob, row: BulkImportRow, stored: dict | None = None) -> dict:
    images = row.images or {}
    final = (stored or {}).get(row.question_id)
    urls = ({c: {"sha256": sha, "url": f"/api/v1/question-images/{final[c]}"} for c, sha in images.items() if c in final} if final else
            {c: {"sha256": sha, "url": f"/api/v1/bulk-imports/{job.job_id}/images/{sha}"} for c, sha in images.items()})
    return {"row_no": row.row_no, "label": rules.row_label(row.row_no, row.data), "question_type": rules.row_type(row.data),
            "import_anyway": bool(row.data.get("import_anyway")), "status": row.status, "data": row.data, "errors": row.errors or [],
            "images": urls,
            "content_format": rules.content_format(row.data, images), "question_id": row.question_id}


@router.get("/bulk-imports/{job_id}/rows")
def rows(job_id: UUID, status: str | None = Query(default=None), page: int = Query(default=1, ge=1),
         page_size: int = Query(default=50, ge=1, le=200), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The preview, page by page in row order; ?status=INVALID lists only the rows that need correcting."""
    job = _job(db, p, job_id)
    stmt = select(BulkImportRow).where(BulkImportRow.job_id == job.job_id)
    if status:
        if status not in ROW_STATUSES:
            raise api_error(422, "INVALID_STATUS", "Unknown row status.")
        stmt = stmt.where(BulkImportRow.status == status)
    res = paginate(db, stmt.order_by(BulkImportRow.row_no), page, page_size)
    stored = _stored_pictures(db, job, res["items"])
    res["items"] = [_row_out(job, r, stored) for r in res["items"]]
    return res


@router.patch("/bulk-imports/{job_id}/rows/{row_no}")
def correct_row(job_id: UUID, row_no: int, body: RowPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Correct a row's text and validate it again immediately (taxonomy, fields and duplicates inside the file; the bank is checked
    again by the next full validation and at import)."""
    job = _job(db, p, job_id, lock=True)
    if job.status not in EDITABLE:
        raise api_error(409, "NOT_EDITABLE", "Rows can be corrected only while the preview is shown.")
    row = db.get(BulkImportRow, (job.job_id, row_no))
    if row is None:
        raise api_error(404, "NOT_FOUND", "Row not found.")
    if row.status == "IMPORTED":
        raise api_error(409, "NOT_EDITABLE", "This row is already imported.")
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise api_error(422, "NO_CHANGE", "Give at least one corrected value.")
    data = {k: v for k, v in row.data.items() if not k.startswith("import_anyway")}   # a corrected row needs a fresh decision (R1)
    row.data = {**data, **{k: v.strip() for k, v in changes.items()}}
    row.edited_by, row.edited_at = p.staff_id, utcnow()
    rules.validate_row(db, row, jobs.taxonomy_context(job))
    _duplicate_in_file(db, job, row)
    jobs.refresh_counts(db, job)
    db.commit()
    return _row_out(job, row)


def _duplicate_in_file(db: Session, job: BulkImportJob, row: BulkImportRow) -> None:
    """A corrected row against the earlier VALID rows with the same content (same key: DUPLICATE; another key: KEY_CONFLICT unless
    imported anyway). The bank is checked again by the next full validation and at import."""
    if row.status != "VALID":
        return
    first: dict[str, dict[str, str]] = {}
    for other in db.scalars(select(BulkImportRow).where(BulkImportRow.job_id == job.job_id, BulkImportRow.row_no < row.row_no,
                                                        BulkImportRow.content_hash == row.content_hash, BulkImportRow.status == "VALID")
                            .order_by(BulkImportRow.row_no).limit(50)):
        first.setdefault(other.content_hash, {}).setdefault(rules.row_key(other.data), rules.row_label(other.row_no, other.data))
    jobs.mark_duplicate(row, first, {})


@router.post("/bulk-imports/{job_id}/rows/{row_no}/import-anyway")
def import_anyway(job_id: UUID, row_no: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """R1: import a KEY_CONFLICT row as a SEPARATE question. The existing question and its key are never changed; the decision and who
    made it are audited now, and the created question is audited at import. Only while the preview is shown."""
    job = _job(db, p, job_id, lock=True)
    if job.status not in EDITABLE:
        raise api_error(409, "NOT_EDITABLE", "Rows can be changed only while the preview is shown.")
    row = db.get(BulkImportRow, (job.job_id, row_no))
    if row is None:
        raise api_error(404, "NOT_FOUND", "Row not found.")
    if row.status != "INVALID" or [e.get("code") for e in row.errors or []] != ["KEY_CONFLICT"]:
        raise api_error(409, "NOT_A_KEY_CONFLICT", "Only a row whose only problem is a different answer key can be imported anyway.")
    conflict = row.errors[0]
    row.data = {**row.data, "import_anyway": True, "import_anyway_by": str(p.staff_id)}
    row.status, row.errors = "VALID", None
    jobs.refresh_counts(db, job)
    _audit(db, p, "bulk_import.key_conflict_import_anyway", job, row=rules.row_label(row.row_no, row.data),
           existing_question_id=conflict.get("question_id"), conflicting_row=conflict.get("row"))
    return _row_out(job, row)


@router.get("/bulk-imports/{job_id}/images/{sha256}")
def preview_image(job_id: UUID, sha256: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A staged picture of the preview, for the people who may see the import (signed URL on S3, streamed otherwise)."""
    job = _job(db, p, job_id)
    image = db.get(BulkImportImage, (job.job_id, sha256)) if len(sha256) == 64 else None
    if image is None or job.cleaned_at is not None:
        raise api_error(404, "NOT_FOUND", "Picture not found.")
    store = get_object_store()
    try:
        url = store.signed_url(image.staging_key, image.mime_type)
        if url:
            return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
        return Response(store.get(image.staging_key), media_type=image.mime_type, headers=PREVIEW_HEADERS)
    except ObjectMissing:
        raise api_error(404, "FILE_MISSING", "The picture is no longer stored.")
    except ObjectStoreError:
        raise api_error(503, "STORE_UNAVAILABLE", "Pictures are temporarily unavailable. Try again shortly.")


# ------------------------------------------------------------------ taxonomy

@router.get("/bulk-imports/{job_id}/taxonomy-issues")
def taxonomy_issues(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Every distinct Chapter/Topic/Subtopic value that matched no record (one decision per spelling, however many rows use it), with
    how many rows use it, suggestions (best first; `preselect`: the one clearly meant, still to be confirmed) and where else the
    name exists. `may_manage`: whether the caller may approve aliases and create records here (otherwise MAP, and request)."""
    job = _job(db, p, job_id)
    may_manage = taxonomy_routes.may_manage(p, job.provider_id)
    issues: dict[tuple, dict] = {}
    rows_with_errors = db.scalars(select(BulkImportRow).where(BulkImportRow.job_id == job.job_id, BulkImportRow.status == "INVALID")
                                  .execution_options(yield_per=2000))
    for row in rows_with_errors:
        for e in row.errors or []:
            if e.get("code") != "TAXONOMY_UNMATCHED":
                continue
            key = (e["level"], e.get("parent_id"), rules.label_key(e["value"]))
            item = issues.setdefault(key, {"level": e["level"], "value": e["value"], "parent_id": e.get("parent_id"),
                                           "suggestions": e.get("suggestions", []), "preselect": e.get("preselect"),
                                           "elsewhere": e.get("elsewhere", []), "may_manage": may_manage, "rows": 0, "example_rows": []})
            item["rows"] += 1
            if len(item["example_rows"]) < 5:
                item["example_rows"].append(row.row_no)
    order = {"CHAPTER": 0, "TOPIC": 1, "SUBTOPIC": 2}
    return sorted(issues.values(), key=lambda i: (order[i["level"]], -i["rows"], i["value"]))


@router.post("/bulk-imports/{job_id}/taxonomy-resolutions", response_model=JobOut)
def resolve_taxonomy(job_id: UUID, body: ResolutionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Map, alias or create one unmatched value, then validate the whole preview again (in the worker)."""
    job = _job(db, p, job_id, lock=True)
    manager = taxonomy_routes.may_manage(p, job.provider_id)
    if body.action != "MAP" and not manager:
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=job.provider_id,
                     details={"permission": "taxonomy.manage", "bulk_import_id": str(job_id), "action": body.action})
        raise api_error(403, "FORBIDDEN", "Only an administrator of this list may approve aliases or create records. Choose a record "
                                          "instead and tick “Remember this spelling” to ask them.")
    if job.status not in EDITABLE:
        raise api_error(409, "NOT_EDITABLE", "Taxonomy values can be resolved only while the preview is shown.")
    node = None
    if body.action in ("MAP", "ALIAS"):
        node = db.get(QuestionTaxonomyNode, body.node_id) if body.node_id else None
        # the record must sit exactly where the value is used: same provider, subject, level and parent
        if (node is None or node.provider_id != job.provider_id or node.subject_id != job.subject_id or node.level != body.level
                or node.parent_id != body.parent_id or node.status != "ACTIVE"):
            raise api_error(422, "INVALID_NODE", "Choose an active record of this subject at the same place in the hierarchy.")
    requested = False
    try:
        if body.action == "MAP":
            mappings = dict(job.taxonomy_mappings or {})
            mappings[rules.TaxonomyContext.mapping_key(body.level, body.parent_id, body.value)] = str(node.node_id)
            job.taxonomy_mappings = mappings
            if body.remember and manager:
                if taxonomy.find(db, job.provider_id, job.subject_id, body.level, body.parent_id, body.value)[0] is None:
                    taxonomy.add_alias(db, node, body.value, p.staff_id, _stored_ip(p))
            elif body.remember:
                requested = taxonomy.request_alias(db, node, body.value, p.staff_id, "BULK_UPLOAD", str(job.job_id)) is not None
        elif body.action == "ALIAS":
            taxonomy.add_alias(db, node, body.value, p.staff_id, _stored_ip(p))
        else:
            node = taxonomy.create_node(db, provider_id=job.provider_id, subject_id=job.subject_id, level=body.level,
                                        parent_id=body.parent_id, name=body.value, staff_id=p.staff_id, ip=_stored_ip(p))
    except TaxonomyError as exc:
        db.rollback()
        raise api_error(exc.status, exc.code, exc.message)
    job.status = "VALIDATE_QUEUED"
    db.commit()
    _audit(db, p, "bulk_import.taxonomy", job, resolution=body.action, level=body.level, value=body.value, node_id=str(node.node_id),
           remembered=body.remember, alias_requested=requested)
    return _out(job)


@router.post("/bulk-imports/{job_id}/revalidate", response_model=JobOut)
def revalidate(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    job = _job(db, p, job_id, lock=True)
    if job.status not in EDITABLE:
        raise api_error(409, "NOT_EDITABLE", "Only a shown preview can be validated again.")
    job.status = "VALIDATE_QUEUED"
    db.commit()
    return _out(job)


# ------------------------------------------------------------------ confirm, cancel, report

@router.post("/bulk-imports/{job_id}/confirm", response_model=JobOut)
def confirm(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Queue the import of every VALID row. A single conditional UPDATE makes this happen once: a repeated or simultaneous click
    finds the job already confirmed and gets the same answer, never a second import."""
    job = _job(db, p, job_id)
    changed = db.execute(update(BulkImportJob).where(BulkImportJob.job_id == job.job_id, BulkImportJob.status == "PREVIEW",
                                                     BulkImportJob.valid_rows > 0)
                         .values(status="IMPORT_QUEUED", confirmed_by=p.staff_id, confirmed_at=utcnow(), confirmed_ip=_stored_ip(p))
                         .execution_options(synchronize_session=False)).rowcount
    db.commit()
    db.refresh(job)
    if changed:
        _audit(db, p, "bulk_import.confirm", job, valid_rows=job.valid_rows, skipped_rows=job.invalid_rows + job.duplicate_rows)
    elif job.confirmed_at is None:
        message = ("There is nothing valid to import yet; correct the rows first." if job.status == "PREVIEW"
                   else "The preview is not ready yet." if job.status not in jobs.TERMINAL else "This import has already ended.")
        raise api_error(409, "NOT_CONFIRMABLE", message)
    return _out(job)


@router.post("/bulk-imports/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Discard an import that has not been confirmed; its staged pictures and file are deleted. Imports already confirmed run to
    the end (questions already created stay, like any other question)."""
    job = _job(db, p, job_id)
    changed = db.execute(update(BulkImportJob).where(BulkImportJob.job_id == job.job_id,
                                                     BulkImportJob.status.in_(("UPLOADED", "PREVIEW", "VALIDATE_QUEUED")))
                         .values(status="CANCELLED", finished_at=utcnow()).execution_options(synchronize_session=False)).rowcount
    db.commit()
    db.refresh(job)
    if not changed and job.status != "CANCELLED":
        raise api_error(409, "NOT_CANCELLABLE", "This import is being processed or has ended; it can no longer be cancelled.")
    if changed:
        jobs.cleanup(db, job, get_object_store())
        _audit(db, p, "bulk_import.cancel", job)
    return _out(job)


def _csv_safe(value) -> str:
    """Spreadsheet programs run cells that start with = + - @ as formulas: such values are written as text."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/bulk-imports/{job_id}/report.csv")
def report(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Every row that was not imported (invalid, duplicate, failed or not yet imported) with each reason, as CSV for Excel."""
    job = _job(db, p, job_id)
    job_ref = job.job_id

    def lines():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Row", "Status", "Field", "Problem"])
        yield "﻿" + buffer.getvalue()                          # BOM: Excel opens UTF-8 (Hindi, symbols) correctly
        for message in job.file_errors or []:
            buffer.seek(0), buffer.truncate()
            writer.writerow(["", "FILE", "", _csv_safe(message)])
            yield buffer.getvalue()
        result = db.scalars(select(BulkImportRow).where(BulkImportRow.job_id == job_ref, BulkImportRow.status != "IMPORTED")
                            .order_by(BulkImportRow.row_no).execution_options(yield_per=2000))
        for row in result:
            for e in row.errors or [{"field": None, "message": "Not imported."}]:
                buffer.seek(0), buffer.truncate()
                writer.writerow([rules.row_label(row.row_no, row.data), row.status, e.get("field") or "", _csv_safe(e.get("message"))])
                yield buffer.getvalue()

    filename = f"import-report-{job.job_id}.csv"
    return StreamingResponse(lines(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"})

