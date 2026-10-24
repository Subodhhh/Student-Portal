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
from app.services import student_ids
from app.services.password_reset import issue_reset_link


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
        try:
            sid = student_ids.allocate(db)      # durable counter: an ID that was ever issued is never issued again
        except student_ids.AllocatorMissing:
            db.rollback()
            raise HTTPException(status_code=503, detail="Student IDs cannot be allocated yet; retry later") from None
        student = Student(student_id=sid, first_name=body.first_name, last_name=body.last_name,
                          email=email, phone=body.phone, status="ACTIVE")
        db.add(student)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()                       # the allocation rolls back too: that number was never issued
            if db.scalar(select(Student).where(Student.email == email)):
                raise HTTPException(status_code=409, detail="A student with this email already exists") from None
            if db.get(Student, sid):            # taken outside the allocator (e.g. inserted by hand): never use it
                student_ids.skip_past(db, sid)
    else:
        raise HTTPException(status_code=503, detail="Could not allocate a student id; retry")
    redis_client.set(cache_key, json.dumps({"student_id": student.student_id}), ex=24 * 3600)
    return _out(student)


SETUP_LINK_LIMIT = 3                 # per STUDENT (staff-initiated); the public forgot-password limit stays per client IP
SETUP_LINK_WINDOW_SECONDS = 60 * 60


@router.post("/students/{student_id}/password-setup-link")
def send_password_setup_link(student_id: str, db: Session = Depends(_db)):
    """Staff-initiated (via the Institute enrollment service): email this student a password setup/reset link, the same token
    mechanism as forgot-password. At most SETUP_LINK_LIMIT per student per hour. The token itself is never returned."""
    s = db.get(Student, student_id)
    if not s or s.deleted_at is not None or s.status != "ACTIVE":
        raise HTTPException(status_code=404, detail="Student not found")
    key = f"auth_setup_link:{student_id}"
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, SETUP_LINK_WINDOW_SECONDS)
    if count > SETUP_LINK_LIMIT:
        raise HTTPException(status_code=429, detail="A setup link was already sent to this student 3 times in the last hour. Try again later.")
    expires_at = issue_reset_link(db, s)
    return {"student_id": student_id, "expires_at": expires_at.isoformat()}
