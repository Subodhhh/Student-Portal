"""Internal API: read projection of Institute-owned reference data.

Providers, branches, batches, enrollments and batch membership are owned by the Institute Portal (its own database). The Student
services still need them locally for eligibility, "My Institutes" and result listings, so the Institute pushes the current state
here. Every write is an idempotent upsert keyed by the Institute's identifiers; batch membership is replaced as a set per
enrollment. Rows are isolated with savepoints: one bad row (for example an unknown student) is reported and never blocks the rest.
Authenticated by X-Service-Key; the whole router answers 404 unless INSTITUTE_SERVICE_API_KEYS is configured."""
import hmac
import logging
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal

log = logging.getLogger("student-enrollment.reference")


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_service_key(x_service_key: str | None = Header(default=None)) -> None:
    keys = [k.strip() for k in settings.institute_service_api_keys.split(",") if k.strip()]
    if not keys:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_service_key or not any(hmac.compare_digest(x_service_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_service_key)])

STATUS = "^(ACTIVE|INACTIVE)$"


class ProviderIn(BaseModel):
    provider_id: UUID
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = None
    status: str = Field(pattern=STATUS)
    deleted_at: datetime | None = None


class BranchIn(BaseModel):
    branch_id: UUID
    provider_id: UUID
    name: str = Field(min_length=1, max_length=255)
    address: str | None = None
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=10)
    status: str = Field(pattern=STATUS)
    deleted_at: datetime | None = None


class BatchIn(BaseModel):
    batch_id: UUID
    branch_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str = Field(pattern=STATUS)


class MembershipIn(BaseModel):
    enrollment_batch_id: UUID
    batch_id: UUID


class EnrollmentIn(BaseModel):
    enrollment_id: str = Field(min_length=1, max_length=36)
    provider_id: UUID
    branch_id: UUID
    student_id: str = Field(min_length=1, max_length=20)
    status: str = Field(pattern=STATUS)
    enrolled_at: datetime
    batches: list[MembershipIn] = Field(default_factory=list, max_length=100)


class ReferenceIn(BaseModel):
    providers: list[ProviderIn] = Field(default_factory=list, max_length=500)
    branches: list[BranchIn] = Field(default_factory=list, max_length=2000)
    batches: list[BatchIn] = Field(default_factory=list, max_length=5000)
    enrollments: list[EnrollmentIn] = Field(default_factory=list, max_length=5000)


def _naive_utc(v: datetime | None) -> datetime | None:
    # The Student tables use TIMESTAMP WITHOUT TIME ZONE holding UTC.
    if v is None:
        return None
    return (v.astimezone(timezone.utc) if v.tzinfo else v).replace(tzinfo=None)


UPSERT_PROVIDER = text("""
    INSERT INTO providers (provider_id, name, email, phone, address, status, deleted_at, created_at, updated_at)
    VALUES (:provider_id, :name, :email, :phone, :address, CAST(:status AS provider_status), :deleted_at, :now, :now)
    ON CONFLICT (provider_id) DO UPDATE SET name = EXCLUDED.name, email = EXCLUDED.email, phone = EXCLUDED.phone,
        address = EXCLUDED.address, status = EXCLUDED.status, deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at
""")
UPSERT_BRANCH = text("""
    INSERT INTO branches (branch_id, provider_id, name, address, city, state, pincode, status, deleted_at, created_at, updated_at)
    VALUES (:branch_id, :provider_id, :name, :address, :city, :state, :pincode, CAST(:status AS branch_status), :deleted_at, :now, :now)
    ON CONFLICT (branch_id) DO UPDATE SET provider_id = EXCLUDED.provider_id, name = EXCLUDED.name, address = EXCLUDED.address,
        city = EXCLUDED.city, state = EXCLUDED.state, pincode = EXCLUDED.pincode, status = EXCLUDED.status,
        deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at
""")
UPSERT_BATCH = text("""
    INSERT INTO batches (batch_id, branch_id, name, description, start_date, end_date, status, created_at, updated_at)
    VALUES (:batch_id, :branch_id, :name, :description, :start_date, :end_date, CAST(:status AS batch_status), :now, :now)
    ON CONFLICT (batch_id) DO UPDATE SET branch_id = EXCLUDED.branch_id, name = EXCLUDED.name, description = EXCLUDED.description,
        start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
""")
UPSERT_ENROLLMENT = text("""
    INSERT INTO enrollments (enrollment_id, provider_id, branch_id, student_id, enrolled_at, status, created_at, updated_at)
    VALUES (:enrollment_id, :provider_id, :branch_id, :student_id, :enrolled_at, CAST(:status AS enrollment_status), :now, :now)
    ON CONFLICT (enrollment_id) DO UPDATE SET provider_id = EXCLUDED.provider_id, branch_id = EXCLUDED.branch_id,
        student_id = EXCLUDED.student_id, enrolled_at = EXCLUDED.enrolled_at, status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
""")


def _apply(db: Session, kind: str, key: str, fn, errors: list[dict]) -> bool:
    sp = db.begin_nested()
    try:
        fn()
        sp.commit()
        return True
    except SQLAlchemyError as exc:
        sp.rollback()
        reason = "constraint" if "violates" in str(getattr(exc, "orig", exc)) else "database"
        # Which constraint, never the row values (no personal data in logs or responses).
        constraint = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
        errors.append({"kind": kind, "id": key, "reason": reason, "constraint": constraint})
        log.warning("reference sync rejected %s %s (%s %s)", kind, key, reason, constraint)
        return False


@router.post("/reference/sync")
def sync_reference(body: ReferenceIn, db: Session = Depends(_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    errors: list[dict] = []
    applied = {"providers": [], "branches": [], "batches": [], "enrollments": []}

    for p in body.providers:
        row = {**p.model_dump(), "email": p.email.lower(), "deleted_at": _naive_utc(p.deleted_at), "now": now}
        if _apply(db, "provider", str(p.provider_id), lambda: db.execute(UPSERT_PROVIDER, row), errors):
            applied["providers"].append(str(p.provider_id))
    for b in body.branches:
        row = {**b.model_dump(), "deleted_at": _naive_utc(b.deleted_at), "now": now}
        if _apply(db, "branch", str(b.branch_id), lambda: db.execute(UPSERT_BRANCH, row), errors):
            applied["branches"].append(str(b.branch_id))
    for b in body.batches:
        row = {**b.model_dump(), "now": now}
        if _apply(db, "batch", str(b.batch_id), lambda: db.execute(UPSERT_BATCH, row), errors):
            applied["batches"].append(str(b.batch_id))
    for e in body.enrollments:
        row = {**e.model_dump(exclude={"batches"}), "enrolled_at": _naive_utc(e.enrolled_at), "now": now}

        def write(e=e, row=row):
            db.execute(UPSERT_ENROLLMENT, row)
            wanted = {m.batch_id: m.enrollment_batch_id for m in e.batches}
            current = {r.batch_id for r in db.execute(
                text("SELECT batch_id FROM enrollment_batches WHERE enrollment_id = :e"), {"e": e.enrollment_id})}
            gone = current - set(wanted)
            if gone:
                db.execute(text("DELETE FROM enrollment_batches WHERE enrollment_id = :e AND batch_id = ANY(:b)"),
                           {"e": e.enrollment_id, "b": list(gone)})
            for batch_id in set(wanted) - current:
                db.execute(text("""INSERT INTO enrollment_batches (enrollment_batch_id, enrollment_id, batch_id, created_at, updated_at)
                                   VALUES (:id, :e, :b, :now, :now) ON CONFLICT (enrollment_batch_id) DO NOTHING"""),
                           {"id": wanted[batch_id], "e": e.enrollment_id, "b": batch_id, "now": now})

        if _apply(db, "enrollment", e.enrollment_id, write, errors):
            applied["enrollments"].append(e.enrollment_id)

    db.commit()
    return {"applied": applied, "errors": errors}
