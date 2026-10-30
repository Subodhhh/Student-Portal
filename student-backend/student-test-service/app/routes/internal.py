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
from sqlalchemy import insert, select
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import paper_eligibility as eligibility_models
from app.models import question as question_models
from app.models import test as test_models
from app.models import test_audience as audience_models
from app.services import paper_eligibility

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
    paper_id: uuid.UUID | None = None         # a Global Test with paper choice: the contribution (Institute submission id)


class PaperIn(BaseModel):
    paper_id: uuid.UUID
    label: str = Field(min_length=1, max_length=255)
    provider_name: str | None = Field(default=None, max_length=255)
    question_count: int = Field(ge=1)
    total_marks: int = Field(ge=0)


class SectionIn(BaseModel):
    position: int = Field(ge=1, le=100)
    subject: str = Field(min_length=1, max_length=100)
    # an exam made for one combination of papers: the subject's status for all its students
    status: str | None = Field(default=None, pattern="^(PAPER|NOT_ENROLLED|NO_SUBMISSION|NOT_APPROVED|NOT_COVERED|CONFLICT)$")
    message: str | None = Field(default=None, max_length=500)
    question_count: int = Field(default=0, ge=0)
    # an exam with paper choice: the subject's papers (each student's status is in the audiences)
    papers: list[PaperIn] | None = Field(default=None, max_length=100)


class CellIn(BaseModel):
    subject: str = Field(min_length=1, max_length=100)
    status: str = Field(pattern="^(PAPER|SELECTABLE|NOT_ENROLLED|NO_SUBMISSION|NOT_APPROVED|NOT_COVERED|CONFLICT)$")
    message: str | None = Field(default=None, max_length=500)
    paper_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class AudienceIn(BaseModel):
    audience_id: str = Field(min_length=1, max_length=64)
    cells: list[CellIn] = Field(min_length=1, max_length=100)


MAX_QUESTIONS = 1000        # an exam without paper choice; one with paper choice is bounded by settings.max_exam_questions


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
    questions: list[QuestionIn] = Field(default_factory=list)
    student_ids: list[str] = Field(default_factory=list, max_length=20000)
    global_occurrence_id: uuid.UUID | None = None      # a unified Global Test exam
    sections: list[SectionIn] | None = Field(default=None, max_length=100)   # unified Global Test: every configured subject, in order
    # A Global Test with paper choice (ONE exam per occurrence): each student's frozen eligibility, as audiences of identical cells
    # (audience_id -> its students). Absent for every other exam.
    audiences: list[AudienceIn] | None = None
    student_audiences: dict[str, list[str]] | None = None

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


def _check_sections(body: ExamIn) -> None:
    """Sections (a unified Global Test) must describe exactly the delivered questions: positions 1..n, every PAPER section's questions
    delivered with that subject and count, no question of any other subject, and an unavailable section without questions. An exam
    without any question is accepted only in that form (a card that explains every subject and cannot be started)."""
    if not body.sections:
        return
    if any(x.status is None for x in body.sections):
        raise HTTPException(status_code=422, detail="Each section needs a status")
    if sorted(x.position for x in body.sections) != list(range(1, len(body.sections) + 1)) or len({x.subject for x in body.sections}) != len(body.sections):
        raise HTTPException(status_code=422, detail="Sections need positions 1..n and distinct subjects")
    delivered: dict[str, int] = {}
    for q in body.questions:
        delivered[q.subject or ""] = delivered.get(q.subject or "", 0) + 1
    expected = {x.subject: x.question_count for x in body.sections if x.status == "PAPER"}
    if delivered != expected or any(x.question_count or not x.message for x in body.sections if x.status != "PAPER"):
        raise HTTPException(status_code=422, detail="Sections do not match the delivered questions")


def _cancel(db: Session, existing) -> dict:
    if existing and existing.status != "INACTIVE":
        existing.status = "INACTIVE"  # attempts/results already recorded are untouched
        db.commit()
    return {"status": "cancelled" if existing else "not_found_noop"}


def _check_questions(body) -> None:
    if [q.order for q in body.questions] != list(range(1, len(body.questions) + 1)):
        raise HTTPException(status_code=422, detail="Question orders must be 1..n without gaps")
    if body.end_at <= body.start_at:
        raise HTTPException(status_code=422, detail="end_at must be after start_at")
    for q in body.questions:
        if set(q.options) != {"A", "B", "C", "D"}:
            raise HTTPException(status_code=422, detail="Each question needs options A-D")


def _reference_missing(exc: IntegrityError) -> HTTPException:
    # The provider/branch has not reached this database's reference projection yet (pushed by the Institute
    # enrollment service). Retryable: the caller's outbox tries again shortly.
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return HTTPException(status_code=409, detail={"code": "REFERENCE_MISSING", "constraint": constraint})


def _store(db: Session, body, sections: list | None, student_ids, extra_rows=()) -> dict | None:
    """Writes the test, its questions (with paper_id when the question has one) and STUDENT audiences, plus `extra_rows`
    (model, rows) bulk-inserted in the same transaction. Returns already_published when a concurrent replay won, else None."""
    db.add(test_models.Test(
        test_id=body.exam_version_id, provider_id=body.provider_id, branch_id=body.branch_id, name=body.name,
        global_occurrence_id=body.global_occurrence_id, sections=sections,
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
        raise _reference_missing(exc)
    for q in body.questions:
        db.add(question_models.Question(
            question_id=uuid.uuid5(NAMESPACE, f"{body.exam_version_id}:{q.order}"), test_id=body.exam_version_id,
            subject=q.subject, paper_id=getattr(q, "paper_id", None), question_order=q.order, question_text=q.text, question_type="MCQ",
            option_a=q.options["A"], option_b=q.options["B"], option_c=q.options["C"], option_d=q.options["D"],
            correct_answer=q.correct, marks=q.marks, negative_marks=q.negative_marks,
        ))
    for sid in dict.fromkeys(student_ids):
        db.add(audience_models.TestAudience(
            test_id=body.exam_version_id, audience_type=audience_models.AudienceType.STUDENT, student_id=sid,
            provider_id=body.provider_id, branch_id=body.branch_id,
        ))
    try:
        db.flush()
        for model, rows in extra_rows:
            if rows:
                db.execute(insert(model), rows)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if db.get(test_models.Test, body.exam_version_id):
            return {"status": "already_published"}   # a concurrent replay won the race
        raise _reference_missing(exc)
    return None


def _check_paper_choice(body: ExamIn) -> dict[str, str]:
    """Validates a Global Test with paper choice completely (nothing is stored on any error); returns student -> audience_id."""
    if (body.student_audiences is None or body.student_ids or not body.sections or any(s.papers is None or s.status for s in body.sections)
            or any(q.paper_id is None or not q.subject for q in body.questions)):
        raise HTTPException(status_code=422, detail="Paper choice needs student_audiences (no student_ids), papers for every section "
                                                    "and a subject and paper for every question")
    if len(body.questions) > settings.max_exam_questions:
        raise HTTPException(status_code=422, detail={"code": "QUESTION_LIMIT_EXCEEDED", "questions": len(body.questions),
                                                      "limit": settings.max_exam_questions})
    _check_questions(body)
    if sorted(s.position for s in body.sections) != list(range(1, len(body.sections) + 1)) or len({s.subject for s in body.sections}) != len(body.sections):
        raise HTTPException(status_code=422, detail="Sections need positions 1..n and distinct subjects")
    subject_of = {}
    for s in body.sections:
        for p in s.papers:
            if p.paper_id in subject_of:
                raise HTTPException(status_code=422, detail="Each paper may appear once")
            subject_of[p.paper_id] = s.subject
    delivered: dict[uuid.UUID, list] = {pid: [] for pid in subject_of}
    for q in body.questions:
        if subject_of.get(q.paper_id) != q.subject:
            raise HTTPException(status_code=422, detail="Each question must belong to a declared paper of its subject")
        delivered[q.paper_id].append(q)
    for s in body.sections:
        for p in s.papers:
            qs = delivered[p.paper_id]
            if len(qs) != p.question_count or sum(q.marks for q in qs) != p.total_marks:
                raise HTTPException(status_code=422, detail="A paper's question count or marks do not match its questions")

    subjects = {s.subject for s in body.sections}
    if len({a.audience_id for a in body.audiences}) != len(body.audiences):
        raise HTTPException(status_code=422, detail="Audience ids must be distinct")
    for a in body.audiences:
        if sorted(c.subject for c in a.cells) != sorted(subjects):
            raise HTTPException(status_code=422, detail="Every audience needs exactly one cell per section subject")
        for c in a.cells:
            n = len(c.paper_ids)
            if (len(set(c.paper_ids)) != n or any(subject_of.get(pid) != c.subject for pid in c.paper_ids)
                    or (c.status == "PAPER" and n != 1) or (c.status == "SELECTABLE" and n < 2)
                    or (c.status not in ("PAPER", "SELECTABLE") and (n or not c.message))):
                raise HTTPException(status_code=422, detail=f"Invalid {c.status} cell for {c.subject}")

    known = {a.audience_id for a in body.audiences}
    student_audience: dict[str, str] = {}
    for audience_id, students in body.student_audiences.items():
        if audience_id not in known:
            raise HTTPException(status_code=422, detail="Unknown audience in student_audiences")
        for sid in students:
            if not 1 <= len(sid) <= 20 or sid in student_audience:
                raise HTTPException(status_code=422, detail="Each student needs a valid id and exactly one audience")
            student_audience[sid] = audience_id
    if not student_audience or len(student_audience) > 20000:
        raise HTTPException(status_code=422, detail="An exam needs 1..20000 students")
    return student_audience


@router.post("/exams", status_code=201)
def receive_exam(body: ExamIn, db: Session = Depends(get_db)):
    """Idempotent by exam_version_id (= test_id); an existing test is never rewritten. A Global Test with paper choice (audiences
    given) is ONE test per occurrence whose questions carry their paper; each student's frozen eligibility (eligible papers per
    subject, or why none) is written once with it and never changed; the student's choice is kept by the attempt service."""
    existing = db.get(test_models.Test, body.exam_version_id)
    if body.operation == "CANCEL":
        return _cancel(db, existing)

    if existing:
        return {"status": "already_published"}  # immutable: replays are acknowledged, not rewritten

    if body.audiences is not None:
        student_audience = _check_paper_choice(body)
        cells = {a.audience_id: a.cells for a in body.audiences}
        position = {x.subject: x.position for x in body.sections}
        now = datetime.now(timezone.utc)
        rows = [{"eligibility_id": uuid.uuid4(), "test_id": body.exam_version_id, "student_id": sid, "subject": c.subject,
                 "position": position[c.subject], "status": c.status, "message": c.message, "paper_id": pid, "created_at": now}
                for sid, audience_id in student_audience.items() for c in cells[audience_id] for pid in (c.paper_ids or [None])]
        # stored sections are the shared configuration (with papers); each student's status per subject comes from the eligibility
        sections = [{"position": x.position, "subject": x.subject, "papers": [p.model_dump(mode="json") for p in x.papers]}
                    for x in sorted(body.sections, key=lambda x: x.position)]
        replay = _store(db, body, sections, student_audience, [(eligibility_models.StudentPaperEligibility, rows)])
        return replay or {"status": "published", "questions": len(body.questions), "students": len(student_audience)}

    if body.student_audiences is not None or any(q.paper_id for q in body.questions) or any(x.papers is not None for x in body.sections or []):
        raise HTTPException(status_code=422, detail="Papers per question or section need audiences (paper choice)")
    if len(body.questions) > MAX_QUESTIONS:
        raise HTTPException(status_code=422, detail=f"An exam may have at most {MAX_QUESTIONS} questions")
    _check_sections(body)
    if not body.questions and not body.sections:
        raise HTTPException(status_code=422, detail="An exam needs at least one question")
    _check_questions(body)

    replay = _store(db, body, [x.model_dump(exclude={"papers"}) for x in body.sections] if body.sections else None, body.student_ids)
    if replay:
        return replay
    return {"status": "published", "questions": len(body.questions), "students": len(set(body.student_ids))}


@router.get("/tests/{test_id}/eligibility/{student_id}")
def student_eligibility(test_id: uuid.UUID, student_id: str, db: Session = Depends(get_db)):
    """A student's frozen eligibility for a test (authoritative, never changes after delivery). paper_selection is false for every
    test without paper choice."""
    test = db.get(test_models.Test, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")
    if not paper_eligibility.has_paper_choice(test.sections):
        return {"test_id": str(test_id), "student_id": student_id, "paper_selection": False, "subjects": []}
    subjects = paper_eligibility.subjects(test.sections, paper_eligibility.rows(db, test_id, student_id))
    if subjects is None:
        raise HTTPException(status_code=404, detail="Student not in this test")
    return {"test_id": str(test_id), "student_id": student_id, "paper_selection": True, "subjects": subjects}


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
