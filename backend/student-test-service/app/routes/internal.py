"""Internal API used by the Institute backend to hand over generated exams.

Writes the existing tests / questions / test_audiences tables, so the student-facing
delivery, attempt and result logic (shared timer, resume, negative marking) is reused
unchanged. Idempotent: the Institute exam_version_id IS the test_id, and an exam that
already exists is never rewritten. Authenticated by X-Service-Key; 404 unless configured."""
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import question as question_models
from app.models import test as test_models
from app.models import test_audience as audience_models

NAMESPACE = uuid.UUID("5f2a3c0e-2a39-4a5e-9a52-7d1a0c6f2b11")


def require_service_key(x_service_key: str | None = Header(default=None)) -> None:
    keys = [k.strip() for k in settings.institute_service_api_keys.split(",") if k.strip()]
    if not keys:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_service_key or not any(hmac.compare_digest(x_service_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


router = APIRouter(prefix="/internal/v1", tags=["Internal"], dependencies=[Depends(require_service_key)])


class QuestionIn(BaseModel):
    order: int = Field(ge=1)
    subject: str | None = Field(default=None, max_length=100)
    text: str = Field(min_length=1)
    options: dict[str, str]
    correct: str = Field(pattern="^[ABCD]$")
    marks: int = Field(ge=0)
    negative_marks: int = Field(ge=0)


class ExamIn(BaseModel):
    operation: str = Field(pattern="^(PUBLISH|CANCEL)$")
    exam_version_id: uuid.UUID
    provider_id: uuid.UUID
    branch_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int = Field(gt=0)
    start_at: datetime
    end_at: datetime
    questions: list[QuestionIn] = Field(default_factory=list, max_length=1000)
    student_ids: list[str] = Field(default_factory=list, max_length=20000)

    @field_validator("start_at", "end_at")
    @classmethod
    def must_have_offset(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must include a UTC offset")
        return v


def _to_session_naive(db: Session, dt: datetime) -> datetime:
    """tests.start_at/end_at are naive and the student queries compare them with NOW() in the *connection's
    session time zone*. So let PostgreSQL express the instant in that same zone: correct whatever the server
    is configured to use (Asia/Kolkata, UTC, ...). Other dialects (tests) fall back to naive UTC."""
    if db.get_bind().dialect.name == "postgresql":
        return db.execute(text("SELECT CAST(:v AS timestamptz) AT TIME ZONE current_setting('TimeZone')"), {"v": dt}).scalar_one()
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@router.post("/exams", status_code=201)
def receive_exam(body: ExamIn, db: Session = Depends(get_db)):
    existing = db.get(test_models.Test, body.exam_version_id)
    if body.operation == "CANCEL":
        if existing and existing.status != "INACTIVE":
            existing.status = "INACTIVE"  # attempts/results already recorded are untouched
            db.commit()
        return {"status": "cancelled" if existing else "not_found_noop"}

    if existing:
        return {"status": "already_published"}  # immutable: replays are acknowledged, not rewritten
    if not body.questions:
        raise HTTPException(status_code=422, detail="An exam needs at least one question")
    if [q.order for q in body.questions] != list(range(1, len(body.questions) + 1)):
        raise HTTPException(status_code=422, detail="Question orders must be 1..n without gaps")
    if body.end_at <= body.start_at:
        raise HTTPException(status_code=422, detail="end_at must be after start_at")
    for q in body.questions:
        if set(q.options) != {"A", "B", "C", "D"}:
            raise HTTPException(status_code=422, detail="Each question needs options A-D")

    db.add(test_models.Test(
        test_id=body.exam_version_id, provider_id=body.provider_id, branch_id=body.branch_id, name=body.name,
        description=body.description, duration_minutes=body.duration_minutes, total_questions=len(body.questions),
        total_marks=sum(q.marks for q in body.questions), start_at=_to_session_naive(db, body.start_at),
        end_at=_to_session_naive(db, body.end_at), status="ACTIVE",
    ))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if db.get(test_models.Test, body.exam_version_id):
            return {"status": "already_published"}
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        raise HTTPException(status_code=409, detail={"code": "REFERENCE_MISSING", "constraint": constraint})
    for q in body.questions:
        db.add(question_models.Question(
            question_id=uuid.uuid5(NAMESPACE, f"{body.exam_version_id}:{q.order}"), test_id=body.exam_version_id,
            subject=q.subject, question_order=q.order, question_text=q.text, question_type="MCQ",
            option_a=q.options["A"], option_b=q.options["B"], option_c=q.options["C"], option_d=q.options["D"],
            correct_answer=q.correct, marks=q.marks, negative_marks=q.negative_marks,
        ))
    for sid in dict.fromkeys(body.student_ids):
        db.add(audience_models.TestAudience(
            test_id=body.exam_version_id, audience_type=audience_models.AudienceType.STUDENT, student_id=sid,
            provider_id=body.provider_id, branch_id=body.branch_id,
        ))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if db.get(test_models.Test, body.exam_version_id):
            return {"status": "already_published"}   # a concurrent replay won the race
        # The provider/branch has not reached this database's reference projection yet (pushed by the Institute
        # enrollment service). Retryable: the caller's outbox tries again shortly.
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        raise HTTPException(status_code=409, detail={"code": "REFERENCE_MISSING", "constraint": constraint})
    return {"status": "published", "questions": len(body.questions), "students": len(set(body.student_ids))}


class KeyChange(BaseModel):
    question_order: int = Field(ge=1)
    correct: str = Field(pattern="^[ABCD]$")


class AnswerKeyIn(BaseModel):
    changes: list[KeyChange] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=5, max_length=1000)
    actor: str = Field(min_length=1, max_length=100)
    correction_ref: str = Field(min_length=8, max_length=100)


@router.post("/exams/{test_id}/answer-key")
def correct_answer_key(test_id: uuid.UUID, body: AnswerKeyIn, db: Session = Depends(get_db)):
    """Corrects the stored correct option of some questions of a delivered test (an Institute-authorized correction).
    All-or-nothing and idempotent (setting a key to its current value is reported as unchanged). The previous values
    are returned so the caller can keep the permanent record; results are re-evaluated by the result service."""
    if len({c.question_order for c in body.changes}) != len(body.changes):
        raise HTTPException(status_code=422, detail="Each question may appear once")
    if not db.get(test_models.Test, test_id):
        raise HTTPException(status_code=404, detail="Test not found")
    rows = {q.question_order: q for q in db.scalars(select(question_models.Question).where(question_models.Question.test_id == test_id))}
    missing = [c.question_order for c in body.changes if c.question_order not in rows]
    if missing:
        raise HTTPException(status_code=404, detail=f"Questions not found: {missing}")
    out = []
    for c in body.changes:
        q = rows[c.question_order]
        previous = q.correct_answer
        if previous == c.correct:
            out.append({"question_order": c.question_order, "previous": previous, "new": c.correct, "unchanged": True})
            continue
        q.correct_answer = c.correct
        out.append({"question_order": c.question_order, "previous": previous, "new": c.correct})
    db.commit()
    return {"test_id": str(test_id), "correction_ref": body.correction_ref, "changes": out}
