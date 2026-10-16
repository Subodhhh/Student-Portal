"""Internal service-to-service API used by the Institute backend.

Student identities are owned by this service: the Institute backend must search for
and create them here rather than writing the students table. Authenticated with a
scoped key in X-Service-Key (never a browser/session credential); the whole router
returns 404 unless INSTITUTE_SERVICE_API_KEYS is configured."""
import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import redis_client
from app.config import settings
from app.database import SessionLocal
from app.models.student import Student


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


# A plain pattern rather than pydantic's email type: this service's pinned requirements do not include
# email-validator, and an internal route must not add a dependency (or a startup failure) to the service.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_service_key)])


class SearchIn(BaseModel):
    email: str | None = Field(default=None, max_length=255, pattern=EMAIL_PATTERN)
    phone: str | None = Field(default=None, max_length=20)


class CreateIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=255, pattern=EMAIL_PATTERN)
    phone: str | None = Field(default=None, max_length=20)


class BatchIn(BaseModel):
    student_ids: list[str] = Field(max_length=200)


def _out(s: Student) -> dict:
    return {"student_id": str(s.student_id), "first_name": s.first_name, "last_name": s.last_name,
            "email": s.email, "phone": s.phone, "status": s.status}


@router.post("/students/search")
def search(body: SearchIn, db: Session = Depends(_db)):
    if not (body.email or body.phone):
        raise HTTPException(status_code=422, detail="email or phone required")
    conds = []
    if body.email:
        conds.append(Student.email == body.email.lower())
    if body.phone:
        conds.append(Student.phone == body.phone)
    from sqlalchemy import or_
    rows = db.scalars(select(Student).where(or_(*conds), Student.deleted_at.is_(None)).limit(10)).all()
    return [_out(s) for s in rows]


@router.get("/students/{student_id}")
def get_student(student_id: str, db: Session = Depends(_db)):
    s = db.get(Student, student_id)
    if not s or s.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Not found")
    return _out(s)


@router.post("/students/batch-get")
def batch_get(body: BatchIn, db: Session = Depends(_db)):
    if not body.student_ids:
        return []
    rows = db.scalars(select(Student).where(Student.student_id.in_(body.student_ids))).all()
    return [_out(s) for s in rows]


def _next_student_id(db: Session) -> str:
    last = db.scalar(select(Student.student_id).where(Student.student_id.like("STU%")).order_by(Student.student_id.desc()).limit(1))
    n = int("".join(ch for ch in str(last) if ch.isdigit()) or 0) + 1 if last else 1
    return f"STU{n:06d}"


@router.post("/students", status_code=201)
def create_student(body: CreateIn, idempotency_key: str | None = Header(default=None), db: Session = Depends(_db)):
    """Creates a global student identity (no password: the student uses the existing
    forgot-password flow to set one). Idempotent per Idempotency-Key; an existing email is
    returned as 409 so callers link the existing identity instead of duplicating it."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key header required")
    cache_key = f"internal_idem:students:{idempotency_key}"
    cached = redis_client.get(cache_key)
    if cached:
        existing = db.get(Student, json.loads(cached)["student_id"])
        if existing:
            return _out(existing)
    email = body.email.lower()
    if db.scalar(select(Student).where(Student.email == email)):
        raise HTTPException(status_code=409, detail="A student with this email already exists")
    for _ in range(5):
        student = Student(student_id=_next_student_id(db), first_name=body.first_name, last_name=body.last_name,
                          email=email, phone=body.phone, status="ACTIVE")
        db.add(student)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if db.scalar(select(Student).where(Student.email == email)):
                raise HTTPException(status_code=409, detail="A student with this email already exists")
    else:
        raise HTTPException(status_code=503, detail="Could not allocate a student id; retry")
    redis_client.set(cache_key, json.dumps({"student_id": student.student_id}), ex=24 * 3600)
    return _out(student)
