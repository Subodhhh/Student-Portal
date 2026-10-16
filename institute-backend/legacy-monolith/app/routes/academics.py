"""Subjects, provider subject enablement and batches."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.security import Principal, api_error, can_see_provider, get_principal
from app.database import get_db
from app.models.academics import BatchSubject, ProviderSubject, Subject
from app.models.org import Batch, Branch
from app.routes._util import escape_like, paginate
from app.routes.orgs import Strict
from app.schemas.common import Page

router = APIRouter(prefix="/api/v1", tags=["Academics"])


class SubjectIn(Strict):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=2, max_length=120)


class SubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    subject_id: UUID
    code: str
    name: str
    status: str


class BatchIn(Strict):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None


class BatchPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    batch_id: UUID
    branch_id: UUID
    name: str
    description: str | None
    start_date: date | None
    end_date: date | None
    status: str


class SubjectRef(Strict):
    subject_id: UUID


@router.post("/subjects", response_model=SubjectOut, status_code=201)
def create_subject(body: SubjectIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.is_global and p.has_perm("subject.manage")):
        p.require(db, "subject.create_global")
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
def list_subjects(q: str | None = Query(default=None, max_length=100), page: int = 1, page_size: int = 50,
                  p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    stmt = select(Subject).where(Subject.status == "ACTIVE")
    if q:
        stmt = stmt.where(Subject.name.ilike(f"%{escape_like(q)}%", escape="\\"))
    return paginate(db, stmt.order_by(Subject.name), page, page_size)


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


@router.post("/branches/{branch_id}/batches", response_model=BatchOut, status_code=201)
def create_batch(branch_id: UUID, body: BatchIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=branch_id)
    if body.start_date and body.end_date and body.end_date < body.start_date:
        raise api_error(422, "INVALID_DATES", "end_date must not precede start_date.")
    b = Batch(branch_id=branch_id, **body.model_dump())
    db.add(b)
    db.commit()
    audit.record(db, "batch.create", "SUCCESS", actor=p.staff_id, target_type="batch", target_id=b.batch_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip)
    return b


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
    return paginate(db, stmt.order_by(Batch.name), page, page_size)


@router.patch("/batches/{batch_id}", response_model=BatchOut)
def update_batch(batch_id: UUID, body: BatchPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b, br = _batch(db, batch_id)
    p.require(db, "batch.manage", provider_id=br.provider_id, branch_id=br.branch_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    if b.start_date and b.end_date and b.end_date < b.start_date:
        db.rollback()
        raise api_error(422, "INVALID_DATES", "end_date must not precede start_date.")
    db.commit()
    audit.record(db, "batch.update", "SUCCESS", actor=p.staff_id, target_type="batch", target_id=batch_id,
                 provider_id=br.provider_id, branch_id=br.branch_id, ip=p.ip)
    return b


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
