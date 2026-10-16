"""Providers and branches."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.security import Principal, api_error, can_see_provider, get_principal
from app.database import get_db
from app.models.org import Branch, Provider
from app.routes._util import escape_like, need_reason, paginate
from app.schemas.common import Page

router = APIRouter(prefix="/api/v1", tags=["Providers & Branches"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderIn(Strict):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=1000)


class ProviderPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=1000)


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider_id: UUID
    name: str
    email: str
    phone: str | None
    address: str | None
    status: str
    created_at: datetime


class BranchIn(Strict):
    name: str = Field(min_length=2, max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=10)


class BranchPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=10)


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    branch_id: UUID
    provider_id: UUID
    name: str
    city: str | None
    state: str | None
    pincode: str | None
    address: str | None
    status: str


class Reason(Strict):
    reason: str


def _provider(db: Session, provider_id: UUID) -> Provider:
    p = db.get(Provider, provider_id)
    if not p or p.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Provider not found.")
    return p


def _branch(db: Session, branch_id: UUID) -> Branch:
    b = db.get(Branch, branch_id)
    if not b or b.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    return b


@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(body: ProviderIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "provider.create")
    prov = Provider(name=body.name, email=body.email.lower(), phone=body.phone, address=body.address,
                    created_ip_address=p.ip if p.ip != "unknown" else None)
    db.add(prov)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "PROVIDER_EXISTS", "A provider with this email already exists.")
    audit.record(db, "provider.create", "SUCCESS", actor=p.staff_id, target_type="provider",
                 target_id=prov.provider_id, provider_id=prov.provider_id, ip=p.ip)
    return prov


@router.get("/providers", response_model=Page[ProviderOut])
def list_providers(
    q: str | None = Query(default=None, max_length=100), status: str | None = Query(default=None, pattern="^(ACTIVE|INACTIVE)$"),
    page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
):
    stmt = select(Provider).where(Provider.deleted_at.is_(None))
    if not p.is_global:
        ids = {a.provider_id for a in p.assignments}
        stmt = stmt.where(Provider.provider_id.in_(ids)) if ids else stmt.where(False)
    if q:
        stmt = stmt.where(Provider.name.ilike(f"%{escape_like(q)}%", escape="\\"))
    if status:
        stmt = stmt.where(Provider.status == status)
    return paginate(db, stmt.order_by(Provider.name), page, page_size)


@router.get("/providers/{provider_id}", response_model=ProviderOut)
def get_provider(provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not can_see_provider(p, provider_id):
        p.require(db, "provider.read", provider_id=provider_id)
    return _provider(db, provider_id)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: UUID, body: ProviderPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "provider.update", provider_id=provider_id)
    prov = _provider(db, provider_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(prov, k, v)
    db.commit()
    audit.record(db, "provider.update", "SUCCESS", actor=p.staff_id, target_type="provider",
                 target_id=provider_id, provider_id=provider_id, ip=p.ip)
    return prov


def _set_provider_status(db, p, provider_id, status, reason, action):
    p.require(db, "provider.deactivate", provider_id=provider_id)
    reason = need_reason(reason)
    prov = _provider(db, provider_id)
    prov.status = status
    db.commit()
    audit.record(db, action, "SUCCESS", actor=p.staff_id, target_type="provider", target_id=provider_id,
                 provider_id=provider_id, reason=reason, ip=p.ip)
    return prov


@router.post("/providers/{provider_id}/deactivate", response_model=ProviderOut)
def deactivate_provider(provider_id: UUID, body: Reason, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _set_provider_status(db, p, provider_id, "INACTIVE", body.reason, "provider.deactivate")


@router.post("/providers/{provider_id}/activate", response_model=ProviderOut)
def activate_provider(provider_id: UUID, body: Reason, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _set_provider_status(db, p, provider_id, "ACTIVE", body.reason, "provider.activate")


# ------------------------------------------------------------------ branches

@router.post("/providers/{provider_id}/branches", response_model=BranchOut, status_code=201)
def create_branch(provider_id: UUID, body: BranchIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(db, "branch.create", provider_id=provider_id)
    _provider(db, provider_id)
    b = Branch(provider_id=provider_id, **body.model_dump(), created_ip_address=p.ip if p.ip != "unknown" else None)
    db.add(b)
    db.commit()
    audit.record(db, "branch.create", "SUCCESS", actor=p.staff_id, target_type="branch", target_id=b.branch_id,
                 provider_id=provider_id, branch_id=b.branch_id, ip=p.ip)
    return b


@router.get("/providers/{provider_id}/branches", response_model=Page[BranchOut])
def list_branches(provider_id: UUID, q: str | None = Query(default=None, max_length=100), page: int = 1, page_size: int = 25,
                  p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not can_see_provider(p, provider_id):
        p.require(db, "branch.read", provider_id=provider_id)
    stmt = select(Branch).where(Branch.provider_id == provider_id, Branch.deleted_at.is_(None))
    allowed = p.scope_branch_ids("branch.read", provider_id)
    if allowed is not None:
        stmt = stmt.where(Branch.branch_id.in_(allowed)) if allowed else stmt.where(False)
    if q:
        stmt = stmt.where(Branch.name.ilike(f"%{escape_like(q)}%", escape="\\"))
    return paginate(db, stmt.order_by(Branch.name), page, page_size)


@router.get("/branches/{branch_id}", response_model=BranchOut)
def get_branch(branch_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b = _branch(db, branch_id)
    allowed = p.scope_branch_ids("branch.read", b.provider_id)
    if allowed is not None and b.branch_id not in allowed:
        p.require(db, "branch.read", provider_id=b.provider_id, branch_id=b.branch_id)
        raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
    return b


@router.patch("/branches/{branch_id}", response_model=BranchOut)
def update_branch(branch_id: UUID, body: BranchPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b = _branch(db, branch_id)
    p.require(db, "branch.update", provider_id=b.provider_id, branch_id=b.branch_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    db.commit()
    audit.record(db, "branch.update", "SUCCESS", actor=p.staff_id, target_type="branch", target_id=branch_id,
                 provider_id=b.provider_id, branch_id=branch_id, ip=p.ip)
    return b


@router.post("/branches/{branch_id}/deactivate", response_model=BranchOut)
def deactivate_branch(branch_id: UUID, body: Reason, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    b = _branch(db, branch_id)
    p.require(db, "branch.deactivate", provider_id=b.provider_id, branch_id=b.branch_id)
    reason = need_reason(body.reason)
    b.status = "INACTIVE"
    db.commit()
    audit.record(db, "branch.deactivate", "SUCCESS", actor=p.staff_id, target_type="branch", target_id=branch_id,
                 provider_id=b.provider_id, branch_id=branch_id, reason=reason, ip=p.ip)
    return b
