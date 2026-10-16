"""Question bank and the paper workflow: upload -> extraction -> validation -> approval ->
publication, with immutable versions and a full status history."""
import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject
from institute_common.models.org import Branch
from institute_common.models.papers import (
    Paper, PaperShare, PaperUpload, PaperVersion, PaperVersionEvent, PaperVersionQuestion,
    Question, QuestionVersion, StoredFile,
)
from institute_common.util import need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from inst_paper.services import documents, extraction
from inst_paper.services.filestore import get_store

router = APIRouter(prefix="/api/v1", tags=["Questions & Papers"])

ALLOWED_TYPES = {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"}
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# extension -> (accepted declared content types, stored content type). The content itself is always verified (magic bytes), never the name.
UPLOAD_KINDS = {
    ".csv": (ALLOWED_TYPES, "text/csv"),
    ".xlsx": ({XLSX_TYPE, "application/octet-stream"}, XLSX_TYPE),
    ".docx": ({DOCX_TYPE, "application/octet-stream"}, DOCX_TYPE),
    ".pdf": ({"application/pdf", "application/octet-stream"}, "application/pdf"),
    ".png": ({"image/png"}, "image/png"),
    ".jpg": ({"image/jpeg"}, "image/jpeg"),
    ".jpeg": ({"image/jpeg"}, "image/jpeg"),
}


# ---------------------------------------------------------------- schemas

class QuestionIn(Strict):
    branch_id: UUID
    subject_id: UUID
    stem: str = Field(min_length=3, max_length=5000)
    option_a: str = Field(min_length=1, max_length=5000)
    option_b: str = Field(min_length=1, max_length=5000)
    option_c: str = Field(min_length=1, max_length=5000)
    option_d: str = Field(min_length=1, max_length=5000)
    correct_option: str = Field(pattern="^[ABCD]$")
    marks_positive: Decimal = Field(gt=0, lt=10000)
    marks_negative: Decimal = Field(ge=0, lt=10000, default=Decimal(0))
    explanation: str | None = Field(default=None, max_length=5000)
    topic: str | None = Field(default=None, max_length=200)


class QuestionVersionIn(Strict):
    stem: str = Field(min_length=3, max_length=5000)
    option_a: str = Field(min_length=1, max_length=5000)
    option_b: str = Field(min_length=1, max_length=5000)
    option_c: str = Field(min_length=1, max_length=5000)
    option_d: str = Field(min_length=1, max_length=5000)
    correct_option: str = Field(pattern="^[ABCD]$")
    marks_positive: Decimal = Field(gt=0, lt=10000)
    marks_negative: Decimal = Field(ge=0, lt=10000, default=Decimal(0))
    explanation: str | None = Field(default=None, max_length=5000)
    topic: str | None = Field(default=None, max_length=200)


class QuestionVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    question_version_id: UUID
    question_id: UUID
    version_no: int
    stem: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_option: str
    explanation: str | None
    topic: str | None
    marks_positive: Decimal
    marks_negative: Decimal


class PaperVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    paper_version_id: UUID
    paper_id: UUID
    version_no: int
    status: str
    question_count: int
    total_marks: Decimal
    content_hash: str
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


class PaperOut(BaseModel):
    paper_id: UUID
    provider_id: UUID
    branch_id: UUID
    subject_id: UUID
    title: str
    created_by: UUID
    latest_version: PaperVersionOut | None


class PaperDetail(PaperOut):
    questions: list[QuestionVersionOut]
    history: list[dict]


class Note(Strict):
    note: str | None = Field(default=None, max_length=1000)


class ReasonIn(Strict):
    reason: str


class OverrideIn(Strict):
    to_status: str = Field(pattern="^(VALIDATED|APPROVED|PUBLISHED|WITHDRAWN)$")
    reason: str


class ShareIn(Strict):
    provider_id: UUID


class QuestionEdit(Strict):
    """A correction of one question of a paper version; only the supplied fields change."""
    position: int = Field(ge=1, le=10000)
    stem: str | None = Field(default=None, min_length=3, max_length=5000)
    option_a: str | None = Field(default=None, min_length=1, max_length=5000)
    option_b: str | None = Field(default=None, min_length=1, max_length=5000)
    option_c: str | None = Field(default=None, min_length=1, max_length=5000)
    option_d: str | None = Field(default=None, min_length=1, max_length=5000)
    correct_option: str | None = Field(default=None, pattern="^[ABCD]$")
    marks_positive: Decimal | None = Field(default=None, gt=0, lt=10000)
    marks_negative: Decimal | None = Field(default=None, ge=0, lt=10000)
    explanation: str | None = Field(default=None, max_length=5000)
    topic: str | None = Field(default=None, max_length=200)

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump(exclude={"position"}).items() if v is not None}

    @model_validator(mode="after")
    def must_change_something(self):
        if not self.changes():
            raise ValueError("an edit must change at least one field")
        return self


class RevisionIn(Strict):
    edits: list[QuestionEdit] = Field(min_length=1, max_length=500)
    reason: str = Field(max_length=1000)

    @model_validator(mode="after")
    def unique_positions(self):
        if len({e.position for e in self.edits}) != len(self.edits):
            raise ValueError("a question may be edited only once per revision")
        return self


# ---------------------------------------------------------------- helpers

def _branch(db: Session, branch_id: UUID) -> Branch:
    b = db.get(Branch, branch_id)
    if not b or b.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    return b


def _check_subject(db: Session, provider_id: UUID, subject_id: UUID) -> None:
    if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id,
                                                  ProviderSubject.subject_id == subject_id)):
        raise api_error(422, "SUBJECT_NOT_ENABLED", "Subject is not enabled for this provider.")


def _scope(p: Principal, perm: str, obj, db: Session, *, owner_only_for_teachers: bool = False) -> None:
    """Authorize `perm` on an object with provider/branch/subject. Teachers are further
    restricted to their teaching assignments (via subject) and, for approve/publish,
    to papers they created themselves."""
    kw = dict(provider_id=obj.provider_id, branch_id=obj.branch_id, subject_id=obj.subject_id)
    if p.can(perm, exclude_roles=(Role.TEACHER,), **kw):
        return
    if p.can(perm, **kw):
        owner = getattr(obj, "created_by", None) or getattr(obj, "owner_staff_id", None)
        if not owner_only_for_teachers or owner == p.staff_id:
            return
    if not p.can(perm, **kw):
        p.require(db, perm, **kw)
    audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=obj.provider_id,
                 branch_id=obj.branch_id, details={"permission": perm, "reason": "not_owner"})
    raise api_error(403, "FORBIDDEN", "Teachers may only do this on papers they created.")


def _paper(db: Session, paper_id: UUID) -> Paper:
    p = db.get(Paper, paper_id)
    if not p:
        raise api_error(404, "NOT_FOUND", "Paper not found.")
    return p


def _pv(db: Session, pv_id: UUID) -> tuple[PaperVersion, Paper]:
    v = db.get(PaperVersion, pv_id)
    if not v:
        raise api_error(404, "NOT_FOUND", "Paper version not found.")
    return v, _paper(db, v.paper_id)


def _event(db, v: PaperVersion, kind, actor, from_s=None, to_s=None, reason=None):
    db.add(PaperVersionEvent(paper_version_id=v.paper_version_id, event_type=kind, from_status=from_s,
                             to_status=to_s, actor_staff_id=actor, reason=reason))


def _hash_questions(items: list[tuple]) -> str:
    h = hashlib.sha256()
    for it in items:
        # Decimals are normalised so 4, 4.0 and 4.00 hash alike (database values come back as 4.00).
        h.update("\x1f".join(format(x.normalize(), "f") if isinstance(x, Decimal) else str(x) for x in it).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def _latest(db: Session, paper_id: UUID) -> PaperVersion | None:
    return db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper_id).order_by(PaperVersion.version_no.desc()).limit(1))


# ---------------------------------------------------------------- question bank

@router.post("/questions", response_model=QuestionVersionOut, status_code=201)
def create_question(body: QuestionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, body.branch_id)
    p.require(db, "question.manage", provider_id=br.provider_id, branch_id=br.branch_id, subject_id=body.subject_id)
    _check_subject(db, br.provider_id, body.subject_id)
    q = Question(provider_id=br.provider_id, branch_id=br.branch_id, subject_id=body.subject_id, owner_staff_id=p.staff_id)
    db.add(q)
    db.flush()
    v = QuestionVersion(question_id=q.question_id, version_no=1, created_by=p.staff_id,
                        **body.model_dump(exclude={"branch_id", "subject_id"}))
    db.add(v)
    db.commit()
    audit.record(db, "question.create", "SUCCESS", actor=p.staff_id, target_type="question", target_id=q.question_id,
                 provider_id=br.provider_id, branch_id=br.branch_id, ip=p.ip)
    return v


@router.post("/questions/{question_id}/versions", response_model=QuestionVersionOut, status_code=201)
def new_question_version(question_id: UUID, body: QuestionVersionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if not q:
        raise api_error(404, "NOT_FOUND", "Question not found.")
    _scope(p, "question.manage", q, db, owner_only_for_teachers=True)
    last = db.scalar(select(func.max(QuestionVersion.version_no)).where(QuestionVersion.question_id == question_id)) or 0
    v = QuestionVersion(question_id=question_id, version_no=last + 1, created_by=p.staff_id, **body.model_dump())
    db.add(v)
    db.commit()  # earlier versions (and any paper that used them) are untouched
    audit.record(db, "question.new_version", "SUCCESS", actor=p.staff_id, target_type="question", target_id=question_id,
                 provider_id=q.provider_id, branch_id=q.branch_id, ip=p.ip)
    return v


@router.get("/branches/{branch_id}/questions", response_model=Page[QuestionVersionOut])
def list_questions(branch_id: UUID, subject_id: UUID | None = None, page: int = 1, page_size: int = 25,
                   p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "question.read", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    latest = select(QuestionVersion.question_id, func.max(QuestionVersion.version_no).label("n")).group_by(QuestionVersion.question_id).subquery()
    stmt = (select(QuestionVersion).join(latest, (latest.c.question_id == QuestionVersion.question_id) & (latest.c.n == QuestionVersion.version_no))
            .join(Question, Question.question_id == QuestionVersion.question_id)
            .where(Question.branch_id == branch_id, Question.status == "ACTIVE"))
    if subject_id:
        stmt = stmt.where(Question.subject_id == subject_id)
    elif not p.can("question.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
        taught = {t.subject_id for t in p.teaching if t.branch_id == branch_id}
        stmt = stmt.where(Question.subject_id.in_(taught)) if taught else stmt.where(False)
    return paginate(db, stmt.order_by(QuestionVersion.created_at.desc()), page, page_size)


# ---------------------------------------------------------------- upload & extraction

@router.post("/branches/{branch_id}/subjects/{subject_id}/papers/upload", status_code=201)
def upload_paper(branch_id: UUID, subject_id: UUID, title: str = Form(min_length=3, max_length=255),
                 paper_id: UUID | None = Form(default=None), file: UploadFile = File(...),
                 p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "paper.upload", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    _check_subject(db, br.provider_id, subject_id)
    name_l = (file.filename or "").lower()
    ext = next((e for e in UPLOAD_KINDS if name_l.endswith(e)), None)
    if ext is None or (file.content_type or "").split(";")[0].strip().lower() not in UPLOAD_KINDS[ext][0]:
        raise api_error(415, "UNSUPPORTED_FILE", "Upload a .csv, one-sheet .xlsx, .docx or text .pdf file (images only where image extraction is enabled).")
    stored_type = UPLOAD_KINDS[ext][1]
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise api_error(413, "FILE_TOO_LARGE", f"File exceeds {settings.max_upload_bytes // 1024} KB.")
    if not data:
        raise api_error(422, "EMPTY_FILE", "The file is empty.")

    existing = None
    if paper_id:
        existing = _paper(db, paper_id)
        if (existing.provider_id, existing.branch_id, existing.subject_id) != (br.provider_id, branch_id, subject_id):
            raise api_error(422, "PAPER_MISMATCH", "Paper belongs to a different branch or subject.")
        _scope(p, "paper.upload", existing, db, owner_only_for_teachers=True)

    key, sha = get_store().save(data)
    sf = StoredFile(provider_id=br.provider_id, storage_key=key, original_name=(file.filename or "upload")[:255],
                    content_type=stored_type, size_bytes=len(data), sha256=sha, uploaded_by=p.staff_id)
    db.add(sf)
    db.flush()
    if ext == ".csv":
        result = extraction.extract_csv(data, settings.max_paper_questions)
    elif ext == ".xlsx":
        result = extraction.extract_xlsx(data, settings.max_paper_questions)
    elif ext == ".docx":
        result = documents.extract_docx(data, settings.max_paper_questions)
    elif ext == ".pdf":
        result = documents.extract_pdf(data, settings.max_paper_questions)
    else:
        result = extraction.extract_image(data, stored_type, extraction.get_ocr_backend(), settings.max_paper_questions, settings.ocr_min_confidence)
    upload = PaperUpload(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, file_id=sf.file_id,
                         uploaded_by=p.staff_id, status="VALIDATED" if result.ok else "REJECTED",
                         question_count=len(result.questions), errors=result.errors or None,
                         uploaded_ip=p.ip if p.ip != "unknown" else None)
    db.add(upload)
    if not result.ok:
        db.commit()  # keep the rejected upload and its precise errors for the uploader
        audit.record(db, "paper.upload", "FAILURE", actor=p.staff_id, target_type="upload", target_id=upload.upload_id,
                     provider_id=br.provider_id, branch_id=branch_id, ip=p.ip, details={"errors": len(result.errors)})
        raise api_error_detail(422, "EXTRACTION_FAILED", "The paper was rejected: no questions were imported.",
                               upload_id=str(upload.upload_id), errors=result.errors[:200])

    content_hash = _hash_questions([(q.stem, *q.options.values(), q.correct, q.marks, q.negative) for q in result.questions])
    dup = db.scalar(select(PaperVersion).join(Paper, Paper.paper_id == PaperVersion.paper_id).where(
        Paper.branch_id == branch_id, Paper.subject_id == subject_id, PaperVersion.content_hash == content_hash,
        PaperVersion.status != "WITHDRAWN"))
    if dup:
        upload.status, upload.errors = "REJECTED", [{"row": None, "field": None, "message": "Identical paper already exists."}]
        db.commit()
        raise api_error_detail(409, "DUPLICATE_PAPER", "An identical paper already exists in this branch and subject.",
                               paper_version_id=str(dup.paper_version_id))

    paper = existing or Paper(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, title=title, created_by=p.staff_id)
    if not existing:
        db.add(paper)
        db.flush()
    version_no = (db.scalar(select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.paper_id)) or 0) + 1
    total = sum((q.marks for q in result.questions), Decimal(0))
    pv = PaperVersion(paper_id=paper.paper_id, version_no=version_no, status="VALIDATED", created_by=p.staff_id,
                      question_count=len(result.questions), total_marks=total, content_hash=content_hash)
    db.add(pv)
    db.flush()
    for pos, q in enumerate(result.questions, start=1):
        bank = Question(provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id, owner_staff_id=p.staff_id)
        db.add(bank)
        db.flush()
        qv = QuestionVersion(question_id=bank.question_id, version_no=1, stem=q.stem, option_a=q.options["A"], option_b=q.options["B"],
                             option_c=q.options["C"], option_d=q.options["D"], correct_option=q.correct, explanation=q.explanation,
                             topic=q.topic, marks_positive=q.marks, marks_negative=q.negative, created_by=p.staff_id)
        db.add(qv)
        db.flush()
        db.add(PaperVersionQuestion(paper_version_id=pv.paper_version_id, position=pos, question_version_id=qv.question_version_id))
    upload.paper_version_id = pv.paper_version_id
    _event(db, pv, "VALIDATED", p.staff_id, None, "VALIDATED")
    db.commit()
    audit.record(db, "paper.upload", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv.paper_version_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip, details={"questions": len(result.questions)})
    return {"upload_id": str(upload.upload_id), "paper_id": str(paper.paper_id), "paper_version_id": str(pv.paper_version_id),
            "version_no": version_no, "status": pv.status, "question_count": pv.question_count, "total_marks": f"{total:.2f}"}


def api_error_detail(status: int, code: str, message: str, **extra):
    from fastapi import HTTPException
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = db.get(PaperUpload, upload_id)
    if not u:
        raise api_error(404, "NOT_FOUND", "Upload not found.")
    _scope(p, "paper.read", u, db)
    return {"upload_id": str(u.upload_id), "status": u.status, "question_count": u.question_count, "errors": u.errors or [],
            "paper_version_id": str(u.paper_version_id) if u.paper_version_id else None, "created_at": u.created_at.isoformat()}


@router.get("/uploads/{upload_id}/file")
def download_upload(upload_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = db.get(PaperUpload, upload_id)
    if not u:
        raise api_error(404, "NOT_FOUND", "Upload not found.")
    _scope(p, "paper.read", u, db)
    sf = db.get(StoredFile, u.file_id)
    try:
        body = get_store().read(sf.storage_key)
    except FileNotFoundError:
        raise api_error(404, "FILE_MISSING", "The stored file is unavailable.")
    audit.record(db, "paper.download", "SUCCESS", actor=p.staff_id, target_type="upload", target_id=upload_id,
                 provider_id=u.provider_id, branch_id=u.branch_id, ip=p.ip)
    ext = next((e for e, (_, t) in UPLOAD_KINDS.items() if t == sf.content_type), ".csv")
    return Response(body, media_type=sf.content_type if sf.content_type in {t for _, t in UPLOAD_KINDS.values()} else "application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="paper{ext}"', "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- papers

def _paper_out(db: Session, paper: Paper) -> PaperOut:
    lv = _latest(db, paper.paper_id)
    return PaperOut(paper_id=paper.paper_id, provider_id=paper.provider_id, branch_id=paper.branch_id, subject_id=paper.subject_id,
                    title=paper.title, created_by=paper.created_by, latest_version=PaperVersionOut.model_validate(lv) if lv else None)


@router.get("/branches/{branch_id}/papers", response_model=Page[PaperOut])
def list_papers(branch_id: UUID, subject_id: UUID | None = None, status: str | None = Query(default=None, max_length=20),
                page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "paper.read", provider_id=br.provider_id, branch_id=branch_id, subject_id=subject_id)
    stmt = select(Paper).where(Paper.branch_id == branch_id)
    if subject_id:
        stmt = stmt.where(Paper.subject_id == subject_id)
    elif not p.can("paper.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
        taught = {t.subject_id for t in p.teaching if t.branch_id == branch_id}
        stmt = stmt.where(Paper.subject_id.in_(taught)) if taught else stmt.where(False)
    if status:
        stmt = stmt.where(Paper.paper_id.in_(select(PaperVersion.paper_id).where(PaperVersion.status == status)))
    res = paginate(db, stmt.order_by(Paper.created_at.desc()), page, page_size)
    res["items"] = [_paper_out(db, x) for x in res["items"]]
    return res


@router.get("/paper-versions/{pv_id}", response_model=PaperDetail)
def get_paper_version(pv_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    v, paper = _pv(db, pv_id)
    _scope(p, "paper.read", paper, db)
    qs = db.scalars(select(QuestionVersion).join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
                    .where(PaperVersionQuestion.paper_version_id == pv_id).order_by(PaperVersionQuestion.position)).all()
    hist = [{"event": e.event_type, "from": e.from_status, "to": e.to_status, "actor": str(e.actor_staff_id),
             "reason": e.reason, "at": e.occurred_at.isoformat()}
            for e in db.scalars(select(PaperVersionEvent).where(PaperVersionEvent.paper_version_id == pv_id).order_by(PaperVersionEvent.occurred_at))]
    base = _paper_out(db, paper).model_dump()
    base["latest_version"] = PaperVersionOut.model_validate(v)
    return PaperDetail(**base, questions=[QuestionVersionOut.model_validate(q) for q in qs], history=hist)


@router.post("/paper-versions/{pv_id}/revisions", response_model=PaperVersionOut, status_code=201)
def revise_paper(pv_id: UUID, body: RevisionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Correct questions of a version (preview -> correct -> approve/publish). Versions are immutable, so a correction creates the NEXT
    version: untouched questions are shared with the source version, corrected ones become new versions of the same bank question.
    The whole resulting paper is validated again; any problem rejects the correction and nothing is created."""
    reason = need_reason(body.reason)
    v, paper = _pv(db, pv_id)
    _scope(p, "paper.create", paper, db, owner_only_for_teachers=True)
    if v.status == "WITHDRAWN":
        raise api_error(409, "INVALID_TRANSITION", "A withdrawn version cannot be revised.")
    rows = db.execute(select(PaperVersionQuestion.position, QuestionVersion).join(
        QuestionVersion, QuestionVersion.question_version_id == PaperVersionQuestion.question_version_id)
        .where(PaperVersionQuestion.paper_version_id == pv_id).order_by(PaperVersionQuestion.position)).all()
    current = {pos: qv for pos, qv in rows}
    edits = {e.position: e for e in body.edits}
    unknown = sorted(set(edits) - set(current))
    if unknown:
        raise api_error_detail(422, "INVALID_POSITION", f"This paper has no question {unknown[0]}.", positions=unknown)

    merged: dict[int, dict] = {}
    changed: set[int] = set()
    for pos, qv in current.items():
        m = {"stem": qv.stem, "option_a": qv.option_a, "option_b": qv.option_b, "option_c": qv.option_c, "option_d": qv.option_d,
             "correct_option": qv.correct_option, "marks_positive": qv.marks_positive, "marks_negative": qv.marks_negative,
             "explanation": qv.explanation, "topic": qv.topic}
        if pos in edits:
            new = {**m, **edits[pos].changes()}
            if any(new[k] != m[k] for k in m):
                changed.add(pos)
            m = new
        merged[pos] = m
    if not changed:
        raise api_error(409, "NO_CHANGE", "These corrections do not change anything.")

    check = extraction.validate_paper(
        [(pos + 1, {"question": m["stem"], "option_a": m["option_a"], "option_b": m["option_b"], "option_c": m["option_c"], "option_d": m["option_d"],
                    "correct": m["correct_option"], "marks": str(m["marks_positive"]), "negative_marks": str(m["marks_negative"]),
                    "explanation": m["explanation"] or "", "topic": m["topic"] or ""}) for pos, m in merged.items()],
        settings.max_paper_questions)
    if not check.ok:
        # error rows are spreadsheet-style (position + 1); report the question position
        errors = [{**e, "row": (e["row"] - 1) if e.get("row") else None} for e in check.errors]
        raise api_error_detail(422, "REVISION_INVALID", "The corrected paper is not valid: nothing was created.", errors=errors[:200])

    content_hash = _hash_questions([(m["stem"], m["option_a"], m["option_b"], m["option_c"], m["option_d"], m["correct_option"],
                                     m["marks_positive"], m["marks_negative"]) for m in merged.values()])
    dup = db.scalar(select(PaperVersion).join(Paper, Paper.paper_id == PaperVersion.paper_id).where(
        Paper.branch_id == paper.branch_id, Paper.subject_id == paper.subject_id, PaperVersion.content_hash == content_hash,
        PaperVersion.status != "WITHDRAWN"))
    if dup:
        raise api_error_detail(409, "DUPLICATE_PAPER", "An identical paper already exists in this branch and subject.",
                               paper_version_id=str(dup.paper_version_id))

    version_no = (db.scalar(select(func.max(PaperVersion.version_no)).where(PaperVersion.paper_id == paper.paper_id)) or 0) + 1
    total = sum((m["marks_positive"] for m in merged.values()), Decimal(0))
    nv = PaperVersion(paper_id=paper.paper_id, version_no=version_no, status="VALIDATED", created_by=p.staff_id,
                      question_count=len(merged), total_marks=total, content_hash=content_hash)
    db.add(nv)
    db.flush()
    for pos, old in current.items():
        if pos in changed:
            m = merged[pos]
            next_no = (db.scalar(select(func.max(QuestionVersion.version_no)).where(QuestionVersion.question_id == old.question_id)) or 0) + 1
            qv = QuestionVersion(question_id=old.question_id, version_no=next_no, created_by=p.staff_id, **m)
            db.add(qv)
            db.flush()
            qvid = qv.question_version_id
        else:
            qvid = old.question_version_id            # shared, never copied: history stays one-to-one with the bank
        db.add(PaperVersionQuestion(paper_version_id=nv.paper_version_id, position=pos, question_version_id=qvid))
    _event(db, nv, "REVISED", p.staff_id, None, "VALIDATED", reason)
    _event(db, v, "REVISION_CREATED", p.staff_id, v.status, v.status, f"version {version_no} created: {reason}")
    db.commit()
    audit.record(db, "paper.revise", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=nv.paper_version_id,
                 provider_id=paper.provider_id, branch_id=paper.branch_id, reason=reason, ip=p.ip,
                 details={"from_version": str(pv_id), "positions": sorted(changed)})
    return nv


def _transition(db: Session, p: Principal, pv_id: UUID, perm: str, expect: str, to: str, kind: str, reason: str | None):
    v, paper = _pv(db, pv_id)
    _scope(p, perm, paper, db, owner_only_for_teachers=True)
    if v.status != expect:
        raise api_error(409, "INVALID_TRANSITION", f"Paper version is {v.status}; expected {expect}.")
    old = v.status
    v.status = to
    if to == "PUBLISHED":
        v.published_at = utcnow()
        for other in db.scalars(select(PaperVersion).where(PaperVersion.paper_id == v.paper_id, PaperVersion.status == "PUBLISHED",
                                                          PaperVersion.paper_version_id != v.paper_version_id)):
            other.status = "SUPERSEDED"  # already-scheduled exams keep pointing at their exact version
            _event(db, other, "SUPERSEDED", p.staff_id, "PUBLISHED", "SUPERSEDED", "newer version published")
    _event(db, v, kind, p.staff_id, old, to, reason)
    db.commit()
    audit.record(db, f"paper.{kind.lower()}", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv_id,
                 provider_id=paper.provider_id, branch_id=paper.branch_id, reason=reason, ip=p.ip)
    return v


@router.post("/paper-versions/{pv_id}/approve", response_model=PaperVersionOut)
def approve(pv_id: UUID, body: Note, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _transition(db, p, pv_id, "paper.approve", "VALIDATED", "APPROVED", "APPROVED", body.note)


@router.post("/paper-versions/{pv_id}/publish", response_model=PaperVersionOut)
def publish(pv_id: UUID, body: Note, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _transition(db, p, pv_id, "paper.publish", "APPROVED", "PUBLISHED", "PUBLISHED", body.note)


@router.post("/paper-versions/{pv_id}/withdraw", response_model=PaperVersionOut)
def withdraw(pv_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    reason = need_reason(body.reason)
    v, paper = _pv(db, pv_id)
    if v.status not in {"PUBLISHED", "APPROVED", "VALIDATED"}:
        raise api_error(409, "INVALID_TRANSITION", "Only draft, approved or published versions can be withdrawn.")
    return _transition(db, p, pv_id, "paper.publish", v.status, "WITHDRAWN", "WITHDRAWN", reason)


@router.post("/paper-versions/{pv_id}/override-status", response_model=PaperVersionOut)
def override_status(pv_id: UUID, body: OverrideIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Super Admin only. Always records actor, reason, before/after in the paper history and audit log."""
    v, paper = _pv(db, pv_id)
    p.require(db, "paper.override_status", provider_id=paper.provider_id, branch_id=paper.branch_id)
    reason = need_reason(body.reason)
    old, v.status = v.status, body.to_status
    if body.to_status == "PUBLISHED" and v.published_at is None:
        v.published_at = utcnow()
    _event(db, v, "OVERRIDE", p.staff_id, old, body.to_status, reason)
    db.commit()
    audit.record(db, "paper.override_status", "SUCCESS", actor=p.staff_id, target_type="paper_version", target_id=pv_id,
                 provider_id=paper.provider_id, branch_id=paper.branch_id, reason=reason, ip=p.ip,
                 details={"from": old, "to": body.to_status})
    return v


# ---------------------------------------------------------------- cross-provider sharing

@router.post("/papers/{paper_id}/shares", status_code=201)
def share_paper(paper_id: UUID, body: ShareIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    paper = _paper(db, paper_id)
    # Releasing content to another provider is a management decision, never a teacher's.
    if not p.can("paper.publish", exclude_roles=(Role.TEACHER,), provider_id=paper.provider_id, branch_id=paper.branch_id):
        p.require(db, "paper.share", provider_id=paper.provider_id, branch_id=paper.branch_id)
    if body.provider_id == paper.provider_id:
        raise api_error(422, "INVALID_SHARE", "Choose a different provider.")
    if not db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper_id, PaperVersion.status == "PUBLISHED")):
        raise api_error(409, "NOT_PUBLISHED", "Only papers with a published version can be shared.")
    share = db.scalar(select(PaperShare).where(PaperShare.paper_id == paper_id, PaperShare.shared_with_provider_id == body.provider_id))
    if share:
        share.status = "ACTIVE"
    else:
        db.add(PaperShare(paper_id=paper_id, owner_provider_id=paper.provider_id, shared_with_provider_id=body.provider_id, granted_by=p.staff_id))
    db.commit()
    audit.record(db, "paper.share", "SUCCESS", actor=p.staff_id, target_type="paper", target_id=paper_id,
                 provider_id=paper.provider_id, ip=p.ip, details={"with": str(body.provider_id)})
    return {"message": "Shared."}


@router.delete("/papers/{paper_id}/shares/{provider_id}", status_code=204)
def unshare_paper(paper_id: UUID, provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    paper = _paper(db, paper_id)
    if not p.can("paper.publish", exclude_roles=(Role.TEACHER,), provider_id=paper.provider_id, branch_id=paper.branch_id):
        p.require(db, "paper.share", provider_id=paper.provider_id, branch_id=paper.branch_id)
    share = db.scalar(select(PaperShare).where(PaperShare.paper_id == paper_id, PaperShare.shared_with_provider_id == provider_id))
    if share:
        share.status = "REVOKED"  # exams already built from it keep their immutable manifest
        db.commit()
        audit.record(db, "paper.unshare", "SUCCESS", actor=p.staff_id, target_type="paper", target_id=paper_id,
                     provider_id=paper.provider_id, ip=p.ip, details={"with": str(provider_id)})
