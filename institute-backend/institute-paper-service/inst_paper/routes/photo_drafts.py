"""Photo papers: build a paper from mobile photos of the printed question paper and, separately, of its answer key.

  1. POST /branches/{b}/subjects/{s}/photo-drafts           create the draft (Idempotency-Key; a retry returns the same draft)
  2. POST /photo-drafts/{id}/files                          one page photo (kind=PAGE) or answer-key photo/file (kind=ANSWER_KEY) per
                                                            request, at a position; OCR runs on it immediately (seconds per page)
     POST /photo-drafts/{id}/files/{f}/read-again {rotate} OCR the stored photo again (optionally turned by the uploader); the
                                                            original stays as uploaded
  3. POST /photo-drafts/{id}/extract                        parse the pages + key into the editable preview (services/photo_drafts.py)
  4. PUT  /photo-drafts/{id}/questions                      corrections (text, options, answers, order, "checked", "needs diagram"),
                                                            every change logged against the OCR text, which is kept as read
  5. POST /photo-drafts/{id}/accept   {confirm: true}       explicit confirmation: every flagged question checked, then the shared
                                                            all-or-nothing validator; success = a VALIDATED paper version that goes
                                                            through the normal approval (nothing is published automatically)

Access: `paper.upload` for the draft's provider/branch/subject; teachers only their own drafts (like their papers). The photos are
served only to that staff (never to students), with the same checks. Images: JPEG/PNG/WebP up to the upload limit each (the portal
downsizes phone photos before sending), at most MAX_PAGES pages; integrity is checked by decoding them."""
import hashlib
import io
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.papers import PaperUpload, PhotoDraft, PhotoDraftFile, StoredFile
from institute_common.util import Strict
from inst_paper.routes.papers import _branch, _check_subject, api_error_detail, persist_validated_paper
from inst_paper.services import extraction
from inst_paper.services import photo_drafts as pd
from inst_paper.services.filestore import get_store

router = APIRouter(prefix="/api/v1", tags=["Photo papers"])
MAX_PAGES = 20
MAX_KEY_FILES = 10
IMAGE_TYPES = {"image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/png": "image/png", "image/webp": "image/webp"}
KEY_TEXT_TYPES = {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"}
MAX_PIXELS = 40_000_000


class DraftIn(Strict):
    title: str = Field(min_length=3, max_length=255)


class QuestionIn(Strict):
    number: int = Field(ge=1, le=10000)
    question: str = Field(default="", max_length=5000)
    option_a: str = Field(default="", max_length=5000)
    option_b: str = Field(default="", max_length=5000)
    option_c: str = Field(default="", max_length=5000)
    option_d: str = Field(default="", max_length=5000)
    correct: str = Field(default="", pattern="^[A-D]?$")
    marks: str = Field(default="", max_length=10)
    negative_marks: str = Field(default="", max_length=10)
    explanation: str = Field(default="", max_length=5000)
    topic: str = Field(default="", max_length=200)
    reviewed: bool = False
    has_diagram: bool = False


class QuestionsIn(Strict):
    questions: list[QuestionIn] = Field(max_length=500)


class AcceptIn(Strict):
    confirm: bool


# ------------------------------------------------------------------ access

def _draft(db: Session, p: Principal, draft_id: UUID, *, write: bool = False, lock: bool = False) -> PhotoDraft:
    stmt = select(PhotoDraft).where(PhotoDraft.draft_id == draft_id)
    d = db.scalar(stmt.with_for_update() if lock else stmt)
    if not d or not p.can("paper.upload", provider_id=d.provider_id, branch_id=d.branch_id, subject_id=d.subject_id):
        raise api_error(404, "NOT_FOUND", "Photo paper not found.")
    if d.created_by != p.staff_id and not p.can("paper.upload", provider_id=d.provider_id, branch_id=d.branch_id, exclude_roles=(Role.TEACHER,)):
        raise api_error(404, "NOT_FOUND", "Photo paper not found.")        # teachers: their own drafts only
    if write and d.status != "DRAFT":
        raise api_error(409, "DRAFT_CLOSED", f"This photo paper is {d.status.lower()}.")
    return d


def _files(db: Session, d: PhotoDraft, kind: str | None = None) -> list[PhotoDraftFile]:
    stmt = select(PhotoDraftFile).where(PhotoDraftFile.draft_id == d.draft_id)
    if kind:
        stmt = stmt.where(PhotoDraftFile.kind == kind)
    return db.scalars(stmt.order_by(PhotoDraftFile.kind, PhotoDraftFile.position)).all()


def _out(db: Session, d: PhotoDraft) -> dict:
    files = _files(db, d)
    return {"draft_id": d.draft_id, "title": d.title, "status": d.status, "provider_id": d.provider_id, "branch_id": d.branch_id,
            "subject_id": d.subject_id, "created_at": d.created_at, "extracted_at": d.extracted_at,
            "accepted_paper_version_id": d.accepted_paper_version_id,
            "files": [{"draft_file_id": f.draft_file_id, "kind": f.kind, "position": f.position, "file_id": f.file_id,
                       "lines": len(f.ocr_lines or []), "ocr_error": f.ocr_error} for f in files],
            "questions": d.questions or [], "issues": d.issues or [], "corrections": len(d.corrections or []),
            "needs_review": [q["number"] for q in (d.questions or []) if q.get("needs_review") and not q.get("reviewed")]}


# ------------------------------------------------------------------ endpoints

@router.post("/branches/{branch_id}/subjects/{subject_id}/photo-drafts", status_code=201)
def create_draft(branch_id: UUID, subject_id: UUID, body: DraftIn, idempotency_key: str = Header(min_length=8, max_length=100),
                 p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "paper.upload", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    _check_subject(db, br.provider_id, subject_id)
    d = db.scalar(select(PhotoDraft).where(PhotoDraft.client_request_id == idempotency_key))
    if d:
        if d.created_by != p.staff_id:
            raise api_error(409, "IDEMPOTENCY_KEY_REUSED", "This request key belongs to another photo paper.")
        return _out(db, d)
    d = PhotoDraft(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, title=body.title.strip(),
                   client_request_id=idempotency_key, created_by=p.staff_id)
    db.add(d)
    db.commit()
    audit.record(db, "paper.photo_draft.create", "SUCCESS", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip)
    return _out(db, d)


@router.get("/photo-drafts")
def list_drafts(branch_id: UUID, status: str = "DRAFT", p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "paper.upload", provider_id=br.provider_id, branch_id=branch_id)
    out = []
    for d in db.scalars(select(PhotoDraft).where(PhotoDraft.branch_id == branch_id, PhotoDraft.status == status).order_by(PhotoDraft.created_at.desc())):
        try:
            _draft(db, p, d.draft_id)
        except Exception:  # noqa: BLE001 - not visible to this caller (another teacher's draft or subject)
            continue
        out.append({"draft_id": d.draft_id, "title": d.title, "status": d.status, "subject_id": d.subject_id, "created_at": d.created_at,
                    "questions": len(d.questions or []), "accepted_paper_version_id": d.accepted_paper_version_id})
    return {"items": out[:100], "total": len(out)}


@router.get("/photo-drafts/{draft_id}")
def get_draft(draft_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _out(db, _draft(db, p, draft_id))


def _check_image(data: bytes, mime: str) -> None:
    """Decode the image (integrity, not just the declared type) and refuse absurd sizes (decompression bombs)."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise api_error(422, "IMAGE_TOO_LARGE", "The photo has too many pixels; take it at a lower resolution.")
            img.verify()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - any decoding failure means the file is not a usable image
        raise api_error(422, "IMAGE_UNREADABLE", "The file is not a readable JPEG/PNG/WebP image.")


@router.post("/photo-drafts/{draft_id}/files", status_code=201)
def add_file(draft_id: UUID, kind: str = Form(pattern="^(PAGE|ANSWER_KEY)$"), position: int = Form(ge=1, le=MAX_PAGES),
             file: UploadFile = File(...), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """One photo (or, for the answer key, a .csv/.txt file) at a position; the same file at the same position again is a no-op
    (retries), a different file replaces that position. OCR runs now, so the preview later only parses stored text."""
    d = _draft(db, p, draft_id, write=True, lock=True)
    mime = (file.content_type or "").split(";")[0].strip().lower()
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise api_error(413, "FILE_TOO_LARGE", f"Each file may be at most {settings.max_upload_bytes // 1024} KB (the portal shrinks photos automatically).")
    if not data:
        raise api_error(422, "EMPTY_FILE", "The file is empty.")
    is_text_key = kind == "ANSWER_KEY" and mime in KEY_TEXT_TYPES and (file.filename or "").lower().endswith((".csv", ".txt"))
    if not is_text_key:
        if mime not in IMAGE_TYPES:
            raise api_error(415, "UNSUPPORTED_FILE", "Upload photos as JPEG, PNG or WebP (an answer key may also be a .csv or .txt file).")
        _check_image(data, mime)
    limit = MAX_PAGES if kind == "PAGE" else MAX_KEY_FILES
    if position > limit:
        raise api_error(422, "TOO_MANY_FILES", f"At most {limit} {'pages' if kind == 'PAGE' else 'answer-key files'}.")
    sha = hashlib.sha256(data).hexdigest()
    current = db.scalar(select(PhotoDraftFile).where(PhotoDraftFile.draft_id == d.draft_id, PhotoDraftFile.kind == kind, PhotoDraftFile.position == position))
    if current:
        old = db.get(StoredFile, current.file_id)
        if old and old.sha256 == sha:
            return _file_out(current)                                        # the same upload retried
    if is_text_key:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise api_error(422, "UNREADABLE_TEXT", "The answer key must be UTF-8 text.")
        lines, error = [[line, 100.0, 100.0] for line in text.splitlines() if line.strip()], None
    else:
        backend = extraction.get_ocr_backend()
        if backend is None or not hasattr(backend, "read_lines"):
            raise api_error(503, "OCR_UNAVAILABLE", "Photo papers need the OCR engine, which is not enabled on this server (OCR_ENGINE=tesseract).")
        lines, error = _read(backend, data, IMAGE_TYPES[mime], kind, None)
    key, _sha = get_store().save(data)
    sf = StoredFile(provider_id=d.provider_id, storage_key=key, original_name=(file.filename or f"{kind.lower()}-{position}")[:255],
                    content_type="text/csv" if is_text_key else IMAGE_TYPES[mime], size_bytes=len(data), sha256=sha, uploaded_by=p.staff_id)
    db.add(sf)
    db.flush()
    if current:
        current.file_id, current.ocr_lines, current.ocr_error = sf.file_id, lines, error
        row = current
    else:
        row = PhotoDraftFile(draft_id=d.draft_id, kind=kind, position=position, file_id=sf.file_id, ocr_lines=lines, ocr_error=error)
        db.add(row)
    db.commit()
    audit.record(db, "paper.photo_draft.file", "SUCCESS", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip, details={"kind": kind, "position": position, "lines": len(lines)})
    return _file_out(row)


def _read(backend, data: bytes, mime: str, kind: str, rotate: int | None) -> tuple[list, str | None]:
    """OCR one photo -> (lines, error for the uploader). A refused or failed photo yields no lines, never partial guesses."""
    try:
        return backend.read_lines(data, mime, "key" if kind == "ANSWER_KEY" else "page", rotate), None
    except extraction.OcrUnreadable as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the file; the uploader replaces the photo
        return [], f"The photo could not be read ({type(exc).__name__}). Retake it flat and well lit."


class ReadAgainIn(Strict):
    rotate: int | None = Field(default=None, description="Clockwise turn that makes the photo upright (0, 90, 180, 270); omitted = automatic")

    @field_validator("rotate")
    @classmethod
    def _quarter_turns(cls, v):
        if v is not None and v not in (0, 90, 180, 270):
            raise ValueError("rotate must be 0, 90, 180 or 270")
        return v


@router.post("/photo-drafts/{draft_id}/files/{draft_file_id}/read-again")
def read_again(draft_id: UUID, draft_file_id: UUID, body: ReadAgainIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """OCR a stored photo again, e.g. after the uploader turned it upright. The uploaded file is not changed; only its OCR text is
    replaced. The preview is rebuilt only when the uploader extracts again (corrections are never overwritten silently)."""
    d = _draft(db, p, draft_id, write=True, lock=True)
    f = db.get(PhotoDraftFile, draft_file_id)
    if not f or f.draft_id != d.draft_id:
        raise api_error(404, "NOT_FOUND", "File not found.")
    sf = db.get(StoredFile, f.file_id)
    if sf.content_type not in IMAGE_TYPES.values():
        raise api_error(422, "NOT_A_PHOTO", "Only photos can be read again; a typed answer key is used as written.")
    backend = extraction.get_ocr_backend()
    if backend is None or not hasattr(backend, "read_lines"):
        raise api_error(503, "OCR_UNAVAILABLE", "Photo papers need the OCR engine, which is not enabled on this server (OCR_ENGINE=tesseract).")
    f.ocr_lines, f.ocr_error = _read(backend, get_store().read(sf.storage_key), sf.content_type, f.kind, body.rotate)
    db.commit()
    audit.record(db, "paper.photo_draft.file", "SUCCESS", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip,
                 details={"kind": f.kind, "position": f.position, "read_again": True, "rotate": body.rotate, "lines": len(f.ocr_lines)})
    return _file_out(f)


def _file_out(f: PhotoDraftFile) -> dict:
    return {"draft_file_id": f.draft_file_id, "kind": f.kind, "position": f.position, "file_id": f.file_id, "lines": len(f.ocr_lines or []),
            "ocr_error": f.ocr_error}


@router.delete("/photo-drafts/{draft_id}/files/{draft_file_id}", status_code=204)
def remove_file(draft_id: UUID, draft_file_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _draft(db, p, draft_id, write=True, lock=True)
    f = db.get(PhotoDraftFile, draft_file_id)
    if not f or f.draft_id != d.draft_id:
        raise api_error(404, "NOT_FOUND", "File not found.")
    db.delete(f)                         # the stored bytes stay (stored_files) for the audit trail of what was uploaded
    db.flush()
    for i, rest in enumerate(_files(db, d, f.kind), start=1):    # keep positions 1..n
        rest.position = i
    db.commit()


class OrderIn(Strict):
    kind: str = Field(pattern="^(PAGE|ANSWER_KEY)$")
    draft_file_ids: list[UUID] = Field(min_length=1, max_length=MAX_PAGES)


@router.put("/photo-drafts/{draft_id}/files/order")
def reorder_files(draft_id: UUID, body: OrderIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Put the uploaded pages (or key files) in this order; the list must name every one of them exactly once."""
    d = _draft(db, p, draft_id, write=True, lock=True)
    files = {f.draft_file_id: f for f in _files(db, d, body.kind)}
    if set(body.draft_file_ids) != set(files) or len(body.draft_file_ids) != len(files):
        raise api_error(422, "ORDER_MISMATCH", "List every uploaded file of this kind exactly once.")
    for i, fid in enumerate(body.draft_file_ids, start=1):      # two passes: the unique (draft, kind, position) never collides
        files[fid].position = 1000 + i
    db.flush()
    for i, fid in enumerate(body.draft_file_ids, start=1):
        files[fid].position = i
    db.commit()
    return _out(db, d)


@router.get("/photo-drafts/{draft_id}/files/{draft_file_id}/content")
def file_content(draft_id: UUID, draft_file_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The photo itself, for the preview next to the extracted text (staff with access to the draft only; never students)."""
    d = _draft(db, p, draft_id)
    f = db.get(PhotoDraftFile, draft_file_id)
    if not f or f.draft_id != d.draft_id:
        raise api_error(404, "NOT_FOUND", "File not found.")
    sf = db.get(StoredFile, f.file_id)
    try:
        body = get_store().read(sf.storage_key)
    except FileNotFoundError:
        raise api_error(404, "FILE_MISSING", "The stored file is unavailable.")
    return Response(body, media_type=sf.content_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/photo-drafts/{draft_id}/extract")
def extract(draft_id: UUID, answer_key_only: bool = False, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Build the preview from the OCR'd pages and the answer key. `answer_key_only` re-matches the key without discarding corrections."""
    d = _draft(db, p, draft_id, write=True, lock=True)
    pages = _files(db, d, "PAGE")
    if not pages:
        raise api_error(422, "NO_PAGES", "Upload at least one photo of the question paper.")
    unreadable = [f.position for f in pages if f.ocr_error]
    if unreadable:
        raise api_error_detail(422, "PAGES_UNREADABLE", "Some photos could not be read: retake or remove them.", pages=unreadable)
    keys = _files(db, d, "ANSWER_KEY")
    key_lines = [line for f in keys for line in (f.ocr_lines or [])] or None
    key_is_text = bool(keys) and all(db.get(StoredFile, f.file_id).content_type == "text/csv" for f in keys)
    questions, issues = pd.build_questions([f.ocr_lines or [] for f in pages], key_lines, settings.ocr_min_confidence, settings.max_paper_questions,
                                           key_is_text=key_is_text)
    first_of: dict[str, int] = {}
    for f in pages:                                    # the same photo uploaded as two pages would duplicate its questions
        sha = db.get(StoredFile, f.file_id).sha256
        if sha in first_of:
            issues.append(f"Page {f.position} is the same photo as page {first_of[sha]}: remove one of them.")
        first_of.setdefault(sha, f.position)
    bad_keys = [f.position for f in keys if f.ocr_error]
    if bad_keys:
        issues.append(f"Answer-key photo {', '.join(map(str, bad_keys))} could not be read: retake it, or enter those answers by hand.")
    if answer_key_only and d.questions:
        fresh = {q["number"]: q for q in questions}
        merged = []
        for q in d.questions:
            f = fresh.get(q["number"])
            if f is not None:
                q = {**q, "correct": f["correct"], "issues": [i for i in q.get("issues", []) if "answer" not in i.lower()] +
                     [i for i in f["issues"] if "answer" in i.lower()]}
                q["needs_review"] = bool(q["issues"]) or q.get("confidence", 0) < settings.ocr_min_confidence
                q["reviewed"] = q.get("reviewed", False) and not any("answer" in i.lower() for i in f["issues"])
            merged.append(q)
        before = d.questions
        d.questions = merged
        d.corrections = (d.corrections or []) + pd.corrections(before, merged, str(p.staff_id), utcnow().isoformat())
    else:
        d.questions, d.extracted = questions, questions
    d.issues, d.extracted_at = issues, utcnow()
    db.commit()
    audit.record(db, "paper.photo_draft.extract", "SUCCESS", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip,
                 details={"questions": len(d.questions), "needs_review": sum(1 for q in d.questions if q["needs_review"]), "answer_key_only": answer_key_only})
    return _out(db, d)


@router.put("/photo-drafts/{draft_id}/questions")
def save_questions(draft_id: UUID, body: QuestionsIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Corrected preview: the full list in the intended order. The OCR text stays in `extracted`; every change is logged."""
    d = _draft(db, p, draft_id, write=True, lock=True)
    if not d.extracted_at:
        raise api_error(409, "NOT_EXTRACTED", "Extract the questions from the photos first.")
    if len({q.number for q in body.questions}) != len(body.questions):
        raise api_error(422, "DUPLICATE_NUMBER", "Each question appears once.")
    previous = {q["number"]: q for q in (d.questions or [])}
    out = []
    for q in body.questions:
        prev = previous.get(q.number, {})
        item = {**prev, **q.model_dump()}
        item.setdefault("confidence", 0.0)
        item.setdefault("page", None)
        item.setdefault("issues", [])
        item["needs_review"] = prev.get("needs_review", True)
        out.append(item)
    log = pd.corrections(d.questions or [], out, str(p.staff_id), utcnow().isoformat())
    d.questions, d.corrections = out, (d.corrections or []) + log
    db.commit()
    if log:
        audit.record(db, "paper.photo_draft.correct", "SUCCESS", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                     provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip, details={"changes": len(log)})
    return _out(db, d)


@router.post("/photo-drafts/{draft_id}/accept")
def accept(draft_id: UUID, body: AcceptIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Explicit confirmation of the preview. All or nothing: every flagged question must have been checked, no question may need a
    diagram, and the shared validator must pass for EVERY question; otherwise nothing is imported and the draft stays as it is."""
    d = _draft(db, p, draft_id, lock=True)
    if d.status == "ACCEPTED":
        return {**_out(db, d), "paper_version_id": d.accepted_paper_version_id}       # retried confirmation
    if d.status != "DRAFT":
        raise api_error(409, "DRAFT_CLOSED", f"This photo paper is {d.status.lower()}.")
    if not body.confirm:
        raise api_error(422, "CONFIRMATION_REQUIRED", "Confirm that you checked the preview.")
    qs = d.questions or []
    if not qs:
        raise api_error(422, "NO_QUESTIONS", "There are no questions to import.")
    unchecked = [q["number"] for q in qs if q.get("needs_review") and not q.get("reviewed")]
    if unchecked:
        raise api_error_detail(422, "REVIEW_REQUIRED", "Check every highlighted question (uncertain text or a problem) before importing.", questions=unchecked)
    diagrams = [q["number"] for q in qs if q.get("has_diagram")]
    if diagrams:
        raise api_error_detail(422, "DIAGRAM_NOT_SUPPORTED", "Questions that need their diagram cannot be imported yet: students cannot be shown "
                               "diagrams. Remove or rewrite them.", questions=diagrams)
    result = extraction.validate_paper(pd.rows_for_validation(qs), settings.max_paper_questions)
    br = _branch(db, d.branch_id)
    pages, keys = _files(db, d, "PAGE"), _files(db, d, "ANSWER_KEY")
    upload = PaperUpload(provider_id=d.provider_id, branch_id=d.branch_id, subject_id=d.subject_id, file_id=pages[0].file_id, title=d.title,
                         answer_key_file_id=keys[0].file_id if keys else None, uploaded_by=p.staff_id,
                         status="VALIDATED" if result.ok else "REJECTED", question_count=len(qs), errors=result.errors or None,
                         uploaded_ip=p.ip if p.ip != "unknown" else None)
    db.add(upload)
    if not result.ok:
        db.commit()
        audit.record(db, "paper.photo_draft.accept", "FAILURE", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                     provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip, details={"errors": len(result.errors)})
        failed = sorted({e["row"] for e in result.errors if e.get("row")})
        raise api_error_detail(422, "EXTRACTION_FAILED", f"Nothing was imported: {len(failed) or 'some'} question(s) still need correcting.",
                               errors=result.errors[:200], questions=failed)
    stored = persist_validated_paper(db, p, br, d.title, None, result, upload)
    d = db.get(PhotoDraft, draft_id)
    d.status, d.accepted_paper_version_id, d.accepted_at = "ACCEPTED", UUID(stored["paper_version_id"]), utcnow()
    db.commit()
    audit.record(db, "paper.photo_draft.accept", "SUCCESS", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip,
                 details={"paper_version_id": stored["paper_version_id"], "questions": len(qs), "corrections": len(d.corrections or [])})
    return {**_out(db, d), "paper_version_id": stored["paper_version_id"]}


@router.post("/photo-drafts/{draft_id}/discard")
def discard(draft_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _draft(db, p, draft_id, write=True, lock=True)
    d.status = "DISCARDED"
    db.commit()
    audit.record(db, "paper.photo_draft.discard", "SUCCESS", actor=p.staff_id, target_type="photo_draft", target_id=d.draft_id,
                 provider_id=d.provider_id, branch_id=d.branch_id, ip=p.ip)
    return _out(db, d)

