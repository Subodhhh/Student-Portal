"""Subjects, provider subject enablement and batches."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, can_see_provider, get_principal
from institute_common.database import get_db
from institute_common.core.class_levels import LABELS, check_academic_year, check_profile
from institute_common.models.academics import BatchAcademicDefault, BatchSubject, ProviderSubject, Subject
from institute_common.models.org import Batch, Branch
from institute_common.util import escape_like, need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page

router = APIRouter(prefix="/api/v1", tags=["Academics"])


class SubjectIn(Strict):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=2, max_length=120)


class SubjectPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")
    reason: str | None = Field(default=None, max_length=500)


class SubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    subject_id: UUID
    code: str
    name: str
    status: str


DEFAULT_FIELDS = ("default_class_level", "default_academic_year", "default_other_description")


class BatchIn(Strict):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None
    # Optional Class / Academic Year suggested when enrolling into this batch (prefill only; see core/class_levels.py).
    default_class_level: str | None = Field(default=None, max_length=20)
    default_academic_year: str | None = Field(default=None, max_length=20)
    default_other_description: str | None = Field(default=None, max_length=200)


class BatchPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")
    # Omitted: unchanged. Explicit null: that default is removed. Existing enrollments are never rewritten.
    default_class_level: str | None = Field(default=None, max_length=20)
    default_academic_year: str | None = Field(default=None, max_length=20)
    default_other_description: str | None = Field(default=None, max_length=200)


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    batch_id: UUID
    branch_id: UUID
    name: str
    description: str | None
    start_date: date | None
    end_date: date | None
    status: str
    default_class_level: str | None = None
    default_class_label: str | None = None
    default_academic_year: str | None = None
    default_other_description: str | None = None


class SubjectRef(Strict):
    subject_id: UUID


def _require_catalogue_admin(db: Session, p: Principal) -> None:
    """The platform subject catalogue is managed by global roles only (providers opt in via provider_subjects)."""
    if not (p.is_global and p.has_perm("subject.manage")):
        p.require(db, "subject.create_global")


@router.post("/subjects", response_model=SubjectOut, status_code=201)
def create_subject(body: SubjectIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_catalogue_admin(db, p)
    s = Subject(code=body.code, name=body.name, created_by=p.staff_id)
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "SUBJECT_EXISTS", "A subject with this code already exists.")
    audit.record(db, "subject.create", "SUCCESS", actor=p.staff_id, target_type="subject", target_id=s.subject_id, ip=p.ip)
    return s


@router.get("/subjects", response_model=Page[SubjectOut])
def list_subjects(q: str | None = Query(default=None, max_length=100), status: str = Query(default="ACTIVE", pattern="^(ACTIVE|INACTIVE|ALL)$"),
                  page: int = 1, page_size: int = 50, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    # Everyone sees the active catalogue; deactivated subjects are only listed for the catalogue administrators.
    if status != "ACTIVE":
        _require_catalogue_admin(db, p)
    stmt = select(Subject)
    if status != "ALL":
        stmt = stmt.where(Subject.status == status)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Subject.name.ilike(like, escape="\\"), Subject.code.ilike(like, escape="\\")))
    return paginate(db, stmt.order_by(Subject.name), page, page_size)


@router.patch("/subjects/{subject_id}", response_model=SubjectOut)
def update_subject(subject_id: UUID, body: SubjectPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Rename, deactivate or reactivate a catalogue subject. There is deliberately no delete: papers, exams, teaching
    assignments and results keep referring to the subject. A deactivated subject can no longer be enabled by a provider
    and disappears from the active catalogue; everything already using it is untouched."""
    _require_catalogue_admin(db, p)
    s = db.get(Subject, subject_id)
    if not s:
        raise api_error(404, "NOT_FOUND", "Subject not found.")
    status_changes = body.status is not None and body.status != s.status
    reason = need_reason(body.reason) if status_changes else body.reason   # validated before anything is changed
    changes: dict[str, dict[str, str]] = {}
    if body.name is not None and body.name != s.name:
        changes["name"] = {"old": s.name, "new": body.name}
        s.name = body.name
    if status_changes:
        changes["status"] = {"old": s.status, "new": body.status}
        s.status = body.status
    if changes:
        db.commit()
        action = ("subject.deactivate" if s.status == "INACTIVE" else "subject.reactivate") if "status" in changes else "subject.update"
        audit.record(db, action, "SUCCESS", actor=p.staff_id, target_type="subject", target_id=s.subject_id, reason=reason,
                     ip=p.ip, details=changes)
    return s


@router.put("/providers/{provider_id}/subjects/{subject_id}", response_model=SubjectOut)
def enable_subject(provider_id: UUID, subject_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "subject.manage", provider_id=provider_id)
    subj = db.get(Subject, subject_id)
    if not subj or subj.status != "ACTIVE":
        raise api_error(404, "NOT_FOUND", "Subject not found.")
    if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id,
                                                  ProviderSubject.subject_id == subject_id)):
        db.add(ProviderSubject(provider_id=provider_id, subject_id=subject_id, created_by=p.staff_id))
        db.commit()
        audit.record(db, "subject.enable", "SUCCESS", actor=p.staff_id, target_type="subject", target_id=subject_id,
                     provider_id=provider_id, ip=p.ip)
    return subj


@router.get("/providers/{provider_id}/subjects", response_model=list[SubjectOut])
def provider_subjects(provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not can_see_provider(p, provider_id):
        p.require(db, "provider.read", provider_id=provider_id)
    return db.scalars(select(Subject).join(ProviderSubject, ProviderSubject.subject_id == Subject.subject_id)
                      .where(ProviderSubject.provider_id == provider_id).order_by(Subject.name)).all()


def _branch(db: Session, branch_id: UUID) -> Branch:
    b = db.get(Branch, branch_id)
    if not b or b.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    return b


def _batch(db: Session, batch_id: UUID) -> tuple[Batch, Branch]:
    b = db.get(Batch, batch_id)
    if not b:
        raise api_error(404, "NOT_FOUND", "Batch not found.")
    return b, _branch(db, b.branch_id)


def _clean_defaults(class_level: str | None, year: str | None, other: str | None) -> tuple[str | None, str | None, str | None]:
    """Validated (class code, academic year, description) for batch defaults; all optional, same rules as an enrollment's profile."""
    try:
        code, desc = check_profile(class_level, other) if class_level and class_level.strip() else (None, None)
        if not code and other and other.strip():
            raise ValueError("default_other_description is only for the default class 'Other'")
        year = check_academic_year(year) if year and year.strip() else None
    except ValueError as exc:
        raise api_error(422, "INVALID_BATCH_DEFAULTS", str(exc)) from None
    return code, year, desc


def _describe_defaults(row: BatchAcademicDefault | None) -> str:
    if row is None:
        return "none"
    cls = (LABELS[row.default_class_level] + (f" ({row.default_other_description})" if row.default_other_description else "")) if row.default_class_level else "-"
    return f"class {cls}, year {row.default_academic_year or '-'}"


def _save_defaults(db: Session, batch_id: UUID, values: tuple[str | None, str | None, str | None], actor) -> tuple[str, str]:
    """Create, change or remove the batch's default row (removed when nothing is set). Returns (before, after) descriptions."""
    row = db.get(BatchAcademicDefault, batch_id)
    before = _describe_defaults(row)
    code, year, desc = values
    if not code and not year:
        if row:
            db.delete(row)
        db.flush()
        return before, "none"
    if row is None:
        row = BatchAcademicDefault(batch_id=batch_id)
        db.add(row)
    row.default_class_level, row.default_academic_year, row.default_other_description, row.updated_by = code, year, desc, actor
    db.flush()
    return before, _describe_defaults(row)


def _out(b: Batch, defaults: BatchAcademicDefault | None) -> BatchOut:
    out = BatchOut.model_validate(b)
    if defaults:
        out.default_class_level, out.default_academic_year = defaults.default_class_level, defaults.default_academic_year
        out.default_class_label = LABELS.get(defaults.default_class_level) if defaults.default_class_level else None
        out.default_other_description = defaults.default_other_description
    return out


@router.post("/branches/{branch_id}/batches", response_model=BatchOut, status_code=201)
def create_batch(branch_id: UUID, body: BatchIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=branch_id)
    if body.start_date and body.end_date and body.end_date < body.start_date:
        raise api_error(422, "INVALID_DATES", "end_date must not precede start_date.")
    defaults = _clean_defaults(body.default_class_level, body.default_academic_year, body.default_other_description)
    b = Batch(branch_id=branch_id, **body.model_dump(exclude=set(DEFAULT_FIELDS)))
    db.add(b)
    db.flush()
    _, after = _save_defaults(db, b.batch_id, defaults, p.staff_id)
    db.commit()
    audit.record(db, "batch.create", "SUCCESS", actor=p.staff_id, target_type="batch", target_id=b.batch_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip, details={"defaults": after} if after != "none" else None)
    return _out(b, db.get(BatchAcademicDefault, b.batch_id))


@router.get("/branches/{branch_id}/batches", response_model=Page[BatchOut])
def list_batches(branch_id: UUID, page: int = 1, page_size: int = 50,
                 p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    stmt = select(Batch).where(Batch.branch_id == branch_id)
    if not p.can("batch.manage", provider_id=br.provider_id, branch_id=branch_id):
        # Teachers: only batches they teach (or every batch if assigned the whole branch).
        rows = [t for t in p.teaching if t.branch_id == branch_id]
        if not rows:
            p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=branch_id)
        if all(t.batch_id is not None for t in rows):
            stmt = stmt.where(Batch.batch_id.in_({t.batch_id for t in rows}))
    res = paginate(db, stmt.order_by(Batch.name), page, page_size)
    rows = {d.batch_id: d for d in db.scalars(select(BatchAcademicDefault).where(BatchAcademicDefault.batch_id.in_([b.batch_id for b in res["items"]] or [None])))}
    res["items"] = [_out(b, rows.get(b.batch_id)) for b in res["items"]]
    return res


@router.patch("/batches/{batch_id}", response_model=BatchOut)
def update_batch(batch_id: UUID, body: BatchPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b, br = _batch(db, batch_id)
    p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=br.branch_id)
    given = body.model_dump(exclude_unset=True)
    for k, v in given.items():
        if k not in DEFAULT_FIELDS:
            setattr(b, k, v)
    if b.start_date and b.end_date and b.end_date < b.start_date:
        db.rollback()
        raise api_error(422, "INVALID_DATES", "end_date must not precede start_date.")
    change = None
    if any(k in given for k in DEFAULT_FIELDS):
        row = db.get(BatchAcademicDefault, batch_id)
        cur = dict(zip(DEFAULT_FIELDS, (row.default_class_level, row.default_academic_year, row.default_other_description) if row else (None, None, None),
                       strict=True))
        cur.update({k: given[k] for k in DEFAULT_FIELDS if k in given})
        if "default_class_level" in given and "default_other_description" not in given and (cur["default_class_level"] or "").strip().upper() != "OTHER":
            cur["default_other_description"] = None
        try:
            values = _clean_defaults(*(cur[k] for k in DEFAULT_FIELDS))
        except Exception:
            db.rollback()
            raise
        change = _save_defaults(db, batch_id, values, p.staff_id)
    db.commit()
    audit.record(db, "batch.update", "SUCCESS", actor=p.staff_id, target_type="batch", target_id=batch_id,
                 provider_id=br.provider_id, branch_id=br.branch_id, ip=p.ip,
                 details={"defaults_before": change[0], "defaults_after": change[1]} if change and change[0] != change[1] else None)
    return _out(b, db.get(BatchAcademicDefault, batch_id))


@router.put("/batches/{batch_id}/subjects/{subject_id}", response_model=SubjectOut)
def map_batch_subject(batch_id: UUID, subject_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b, br = _batch(db, batch_id)
    p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=br.branch_id)
    if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == br.provider_id,
                                                  ProviderSubject.subject_id == subject_id)):
        raise api_error(422, "SUBJECT_NOT_ENABLED", "Subject is not enabled for this provider.")
    if not db.scalar(select(BatchSubject).where(BatchSubject.batch_id == batch_id, BatchSubject.subject_id == subject_id)):
        db.add(BatchSubject(batch_id=batch_id, subject_id=subject_id, created_by=p.staff_id))
        db.commit()
        audit.record(db, "batch.map_subject", "SUCCESS", actor=p.staff_id, target_type="batch", target_id=batch_id,
                     provider_id=br.provider_id, branch_id=br.branch_id, ip=p.ip, details={"subject_id": str(subject_id)})
    return db.get(Subject, subject_id)


@router.get("/batches/{batch_id}/subjects", response_model=list[SubjectOut])
def batch_subjects(batch_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b, br = _batch(db, batch_id)
    if not (p.can("batch.manage", provider_id=br.provider_id, branch_id=br.branch_id)
            or p.can("exam.read", provider_id=br.provider_id, branch_id=br.branch_id, batch_id=batch_id)):
        p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=br.branch_id)
    return db.scalars(select(Subject).join(BatchSubject, BatchSubject.subject_id == Subject.subject_id)
                      .where(BatchSubject.batch_id == batch_id).order_by(Subject.name)).all()


@router.delete("/batches/{batch_id}/subjects/{subject_id}", status_code=204)
def unmap_batch_subject(batch_id: UUID, subject_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b, br = _batch(db, batch_id)
    p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=br.branch_id)
    row = db.scalar(select(BatchSubject).where(BatchSubject.batch_id == batch_id, BatchSubject.subject_id == subject_id))
    if row:
        db.delete(row)
        db.commit()
        audit.record(db, "batch.unmap_subject", "SUCCESS", actor=p.staff_id, target_type="batch", target_id=batch_id,
                     provider_id=br.provider_id, branch_id=br.branch_id, ip=p.ip, details={"subject_id": str(subject_id)})
