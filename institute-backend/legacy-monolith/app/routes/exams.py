"""Exam plans, generation, exams, composite papers and assignments."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.security import Principal, api_error, get_principal
from app.database import get_db, utcnow
from app.models.academics import ProviderSubject
from app.models.exams import (
    Exam, ExamGenerationEvent, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanSubject,
    ExamVersion, ExamVersionPaper, StudentExamAssignment,
)
from app.models.org import Batch, Branch
from app.models.papers import Paper, PaperVersion
from app.routes._util import need_reason, paginate
from app.routes.orgs import Strict
from app.schemas.common import Page
from app.core.roles import Role
from app.services import exam_generation as gen
from app.services import assignment_sync
from app.services.attempt_client import AttemptClient, get_attempt_client
from app.services.internal_http import InternalServiceError
from app.services.result_client import ResultClient, ResultServiceError, get_result_client
from app.services.student_client import StudentClient, StudentServiceError, get_student_client

router = APIRouter(prefix="/api/v1", tags=["Exams"])


class SlotIn(Strict):
    subject_id: UUID
    paper_id: UUID | None = None
    paper_version_id: UUID | None = None

    @model_validator(mode="after")
    def one_rule(self):
        if bool(self.paper_id) == bool(self.paper_version_id):
            raise ValueError("provide exactly one of paper_id or paper_version_id")
        return self


class PlanIn(Strict):
    name: str = Field(min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int = Field(gt=0, le=720)
    start_at: datetime
    end_at: datetime
    generate_at: datetime | None = None
    auto_publish: bool = True
    batch_ids: list[UUID] = Field(min_length=1, max_length=50)
    slots: list[SlotIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def window(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.generate_at and self.generate_at > self.start_at:
            raise ValueError("generate_at must not be after start_at")
        return self


class PlanPatch(Strict):
    name: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int | None = Field(default=None, gt=0, le=720)
    start_at: datetime | None = None
    end_at: datetime | None = None
    generate_at: datetime | None = None
    auto_publish: bool | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


class GenerateIn(Strict):
    start_at: datetime | None = None
    end_at: datetime | None = None


class PlanOut(BaseModel):
    plan_id: UUID
    provider_id: UUID
    branch_id: UUID
    name: str
    description: str | None
    duration_minutes: int
    start_at: datetime
    end_at: datetime
    generate_at: datetime | None
    auto_publish: bool
    status: str
    batch_ids: list[UUID]
    slots: list[SlotIn]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    job_id: UUID
    plan_id: UUID
    run_key: str
    status: str
    attempts: int
    exam_id: UUID | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class ExamOut(BaseModel):
    exam_id: UUID
    plan_id: UUID
    provider_id: UUID
    branch_id: UUID
    name: str
    status: str
    exam_version_id: UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    duration_minutes: int | None = None
    total_questions: int | None = None
    total_marks: Decimal | None = None
    missing_subjects: list[dict] = []
    papers: list[dict] = []
    assigned_students: int = 0
    delivery_status: str | None = None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    assignment_id: UUID
    student_id: str
    batch_id: UUID | None
    status: str


class CancelIn(Strict):
    reason: str


def _plan(db: Session, plan_id: UUID) -> ExamPlan:
    p = db.get(ExamPlan, plan_id)
    if not p:
        raise api_error(404, "NOT_FOUND", "Exam plan not found.")
    return p


def _branch(db: Session, branch_id: UUID) -> Branch:
    b = db.get(Branch, branch_id)
    if not b or b.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    return b


def _plan_out(db: Session, plan: ExamPlan) -> PlanOut:
    batches = [b for (b,) in db.execute(select(ExamPlanBatch.batch_id).where(ExamPlanBatch.plan_id == plan.plan_id))]
    slots = db.scalars(select(ExamPlanSubject).where(ExamPlanSubject.plan_id == plan.plan_id).order_by(ExamPlanSubject.position)).all()
    return PlanOut(**{c: getattr(plan, c) for c in ("plan_id", "provider_id", "branch_id", "name", "description", "duration_minutes",
                                                     "start_at", "end_at", "generate_at", "auto_publish", "status")},
                   batch_ids=batches, slots=[SlotIn(subject_id=s.subject_id, paper_id=s.paper_id, paper_version_id=s.paper_version_id) for s in slots])


def _validate_slots(db: Session, provider_id: UUID, slots: list[SlotIn]) -> None:
    if len({s.subject_id for s in slots}) != len(slots):
        raise api_error(422, "DUPLICATE_SUBJECT", "Each subject may appear once in a plan.")
    for s in slots:
        if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == provider_id, ProviderSubject.subject_id == s.subject_id)):
            raise api_error(422, "SUBJECT_NOT_ENABLED", "A subject in the plan is not enabled for this provider.")
        if s.paper_version_id:
            v = db.get(PaperVersion, s.paper_version_id)
            paper = db.get(Paper, v.paper_id) if v else None
        else:
            paper = db.get(Paper, s.paper_id)
        # Same message for "missing" and "not yours": do not reveal other providers' content.
        if not paper or paper.subject_id != s.subject_id or not gen.authorized_source(db, provider_id, paper):
            raise api_error(422, "PAPER_NOT_AVAILABLE", "A paper in the plan does not exist or is not available to this provider.")


def _write_children(db: Session, plan: ExamPlan, batch_ids: list[UUID], slots: list[SlotIn]) -> None:
    for bid in dict.fromkeys(batch_ids):
        b = db.get(Batch, bid)
        if not b or b.branch_id != plan.branch_id or b.status != "ACTIVE":
            raise api_error(422, "INVALID_BATCH", "A batch does not belong to this branch or is inactive.")
        db.add(ExamPlanBatch(plan_id=plan.plan_id, batch_id=bid))
    for pos, s in enumerate(slots, start=1):
        db.add(ExamPlanSubject(plan_id=plan.plan_id, position=pos, subject_id=s.subject_id, paper_id=s.paper_id,
                               paper_version_id=s.paper_version_id))


@router.post("/branches/{branch_id}/exam-plans", response_model=PlanOut, status_code=201)
def create_plan(branch_id: UUID, body: PlanIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "examplan.manage", provider_id=br.provider_id, branch_id=branch_id)
    _validate_slots(db, br.provider_id, body.slots)
    plan = ExamPlan(provider_id=br.provider_id, branch_id=branch_id, created_by=p.staff_id,
                    **body.model_dump(exclude={"batch_ids", "slots"}))
    db.add(plan)
    db.flush()
    _write_children(db, plan, body.batch_ids, body.slots)
    db.commit()
    audit.record(db, "examplan.create", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan.plan_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip)
    return _plan_out(db, plan)


@router.get("/branches/{branch_id}/exam-plans", response_model=Page[PlanOut])
def list_plans(branch_id: UUID, page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "examplan.manage", provider_id=br.provider_id, branch_id=branch_id)
    res = paginate(db, select(ExamPlan).where(ExamPlan.branch_id == branch_id).order_by(ExamPlan.start_at.desc()), page, page_size)
    res["items"] = [_plan_out(db, x) for x in res["items"]]
    return res


@router.get("/exam-plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    p.require(db, "examplan.manage", provider_id=plan.provider_id, branch_id=plan.branch_id)
    return _plan_out(db, plan)


@router.patch("/exam-plans/{plan_id}", response_model=PlanOut)
def update_plan(plan_id: UUID, body: PlanPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Edits affect only FUTURE generations; exams already generated keep their immutable manifest."""
    plan = _plan(db, plan_id)
    p.require(db, "examplan.manage", provider_id=plan.provider_id, branch_id=plan.branch_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(plan, k, v)
    if plan.end_at <= plan.start_at:
        db.rollback()
        raise api_error(422, "INVALID_WINDOW", "end_at must be after start_at.")
    db.commit()
    audit.record(db, "examplan.update", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan_id,
                 provider_id=plan.provider_id, branch_id=plan.branch_id, ip=p.ip)
    return _plan_out(db, plan)


@router.put("/exam-plans/{plan_id}/slots", response_model=PlanOut)
def replace_slots(plan_id: UUID, slots: list[SlotIn], p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    p.require(db, "examplan.manage", provider_id=plan.provider_id, branch_id=plan.branch_id)
    if not slots or len(slots) > 20:
        raise api_error(422, "INVALID_SLOTS", "A plan needs between 1 and 20 subject slots.")
    _validate_slots(db, plan.provider_id, slots)
    for old in db.scalars(select(ExamPlanSubject).where(ExamPlanSubject.plan_id == plan_id)):
        db.delete(old)
    db.flush()
    for pos, s in enumerate(slots, start=1):
        db.add(ExamPlanSubject(plan_id=plan_id, position=pos, subject_id=s.subject_id, paper_id=s.paper_id, paper_version_id=s.paper_version_id))
    db.commit()
    audit.record(db, "examplan.slots", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan_id,
                 provider_id=plan.provider_id, branch_id=plan.branch_id, ip=p.ip)
    return _plan_out(db, plan)


@router.post("/exam-plans/{plan_id}/generate", response_model=JobOut)
def generate_exam(plan_id: UUID, body: GenerateIn, idempotency_key: str | None = Header(default=None, max_length=100),
                  p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    p.require(db, "exam.generate", provider_id=plan.provider_id, branch_id=plan.branch_id)
    start, end = body.start_at or plan.start_at, body.end_at or plan.end_at
    if end <= start:
        raise api_error(422, "INVALID_WINDOW", "end_at must be after start_at.")
    run_key = f"manual:{idempotency_key}" if idempotency_key else f"manual:{start.isoformat()}"
    try:
        job = gen.generate(db, plan_id, run_key, actor=p.staff_id, start_at=body.start_at, end_at=body.end_at)
    except ValueError as exc:
        raise api_error(409, "PLAN_INACTIVE", str(exc))
    return job


@router.get("/exam-plans/{plan_id}/jobs", response_model=Page[JobOut])
def plan_jobs(plan_id: UUID, page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    p.require(db, "exam.generate", provider_id=plan.provider_id, branch_id=plan.branch_id)
    return paginate(db, select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan_id).order_by(ExamGenerationJob.started_at.desc()), page, page_size)


@router.get("/jobs/{job_id}/events")
def job_events(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    job = db.get(ExamGenerationJob, job_id)
    if not job:
        raise api_error(404, "NOT_FOUND", "Job not found.")
    plan = _plan(db, job.plan_id)
    p.require(db, "exam.generate", provider_id=plan.provider_id, branch_id=plan.branch_id)
    return [{"type": e.event_type, "details": e.details, "at": e.occurred_at.isoformat()}
            for e in db.scalars(select(ExamGenerationEvent).where(ExamGenerationEvent.job_id == job_id).order_by(ExamGenerationEvent.occurred_at))]


# ------------------------------------------------------------------ exams

def _exam(db: Session, exam_id: UUID) -> Exam:
    e = db.get(Exam, exam_id)
    if not e:
        raise api_error(404, "NOT_FOUND", "Exam not found.")
    return e


def _exam_out(db: Session, exam: Exam) -> ExamOut:
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam.exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    out = ExamOut(exam_id=exam.exam_id, plan_id=exam.plan_id, provider_id=exam.provider_id, branch_id=exam.branch_id,
                  name=exam.name, status=exam.status)
    if ev:
        papers = db.scalars(select(ExamVersionPaper).where(ExamVersionPaper.exam_version_id == ev.exam_version_id).order_by(ExamVersionPaper.position)).all()
        ob = db.scalar(select(ExamOutbox).where(ExamOutbox.exam_version_id == ev.exam_version_id, ExamOutbox.operation == "PUBLISH"))
        out.exam_version_id, out.start_at, out.end_at = ev.exam_version_id, ev.start_at, ev.end_at
        out.duration_minutes, out.total_questions, out.total_marks = ev.duration_minutes, ev.total_questions, ev.total_marks
        out.missing_subjects = ev.missing_subjects or []
        # Manifest shows provenance (source provider, version) but never question content.
        out.papers = [{"subject_id": str(x.subject_id), "paper_version_id": str(x.paper_version_id),
                       "source_provider_id": str(x.source_provider_id), "question_count": x.question_count} for x in papers]
        out.assigned_students = db.scalar(select(func.count()).select_from(StudentExamAssignment).where(StudentExamAssignment.exam_version_id == ev.exam_version_id)) or 0
        out.delivery_status = ob.status if ob else "NOT_PUBLISHED"
    return out


@router.get("/branches/{branch_id}/exams", response_model=Page[ExamOut])
def list_exams(branch_id: UUID, status: str | None = None, page: int = 1, page_size: int = 25,
               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "exam.read", provider_id=br.provider_id, branch_id=branch_id)
    stmt = select(Exam).where(Exam.branch_id == branch_id)
    if status in {"SCHEDULED", "PUBLISHED", "CANCELLED"}:
        stmt = stmt.where(Exam.status == status)
    res = paginate(db, stmt.order_by(Exam.created_at.desc()), page, page_size)
    res["items"] = [_exam_out(db, e) for e in res["items"]]
    return res


@router.get("/exams/{exam_id}", response_model=ExamOut)
def get_exam(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _exam(db, exam_id)
    p.require(db, "exam.read", provider_id=e.provider_id, branch_id=e.branch_id)
    return _exam_out(db, e)


@router.post("/exams/{exam_id}/publish", response_model=ExamOut)
def publish_exam(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _exam(db, exam_id)
    p.require(db, "exam.publish", provider_id=e.provider_id, branch_id=e.branch_id)
    if e.status != "SCHEDULED":
        raise api_error(409, "INVALID_TRANSITION", f"Exam is {e.status}.")
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    e.status, ev.published_at = "PUBLISHED", utcnow()
    db.add(ExamOutbox(exam_version_id=ev.exam_version_id, operation="PUBLISH"))
    db.commit()
    audit.record(db, "exam.publish", "SUCCESS", actor=p.staff_id, target_type="exam", target_id=exam_id,
                 provider_id=e.provider_id, branch_id=e.branch_id, ip=p.ip)
    return _exam_out(db, e)


@router.post("/exams/{exam_id}/cancel", response_model=ExamOut)
def cancel_exam(exam_id: UUID, body: CancelIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _exam(db, exam_id)
    p.require(db, "exam.schedule", provider_id=e.provider_id, branch_id=e.branch_id)
    reason = need_reason(body.reason)
    if e.status == "CANCELLED":
        raise api_error(409, "INVALID_TRANSITION", "Exam is already cancelled.")
    was_published = e.status == "PUBLISHED"
    e.status, e.cancelled_reason = "CANCELLED", reason
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    if was_published and ev:
        db.add(ExamOutbox(exam_version_id=ev.exam_version_id, operation="CANCEL"))
    db.commit()
    audit.record(db, "exam.cancel", "SUCCESS", actor=p.staff_id, target_type="exam", target_id=exam_id,
                 provider_id=e.provider_id, branch_id=e.branch_id, reason=reason, ip=p.ip)
    return _exam_out(db, e)


@router.get("/exams/{exam_id}/assignments", response_model=Page[AssignmentOut])
def exam_assignments(exam_id: UUID, page: int = 1, page_size: int = 50, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _exam(db, exam_id)
    p.require(db, "exam.read", provider_id=e.provider_id, branch_id=e.branch_id)
    return paginate(db, select(StudentExamAssignment).where(StudentExamAssignment.exam_id == exam_id).order_by(StudentExamAssignment.student_id), page, page_size)


# ------------------------------------------------------------------ results & analytics

@router.get("/exams/{exam_id}/results")
def exam_results(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                 rc: ResultClient = Depends(get_result_client), sc: StudentClient = Depends(get_student_client)):
    """Results are evaluated by the Student services; this reads them through the internal API.
    Teachers see only students of the batches they teach (whole branch if assigned to it)."""
    e = _exam(db, exam_id)
    p.require(db, "result.read", provider_id=e.provider_id, branch_id=e.branch_id)
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    if e.status != "PUBLISHED" or not ev:
        raise api_error(409, "NOT_PUBLISHED", "Results exist only for published exams.")
    try:
        rows = rc.exam_results(str(ev.exam_version_id))
    except ResultServiceError:
        raise api_error(503, "RESULT_SERVICE_UNAVAILABLE", "Results are temporarily unavailable.")

    assignments = {a.student_id: a for a in db.scalars(select(StudentExamAssignment).where(StudentExamAssignment.exam_version_id == ev.exam_version_id))}
    allowed = set(assignments)
    if not p.can("result.read", exclude_roles=(Role.TEACHER,), provider_id=e.provider_id, branch_id=e.branch_id):
        taught = [t for t in p.teaching if t.branch_id == e.branch_id]
        if not any(t.batch_id is None for t in taught):
            batches = {t.batch_id for t in taught}
            allowed = {sid for sid, a in assignments.items() if a.batch_id in batches}
    rows = [r for r in rows if r["student_id"] in allowed]          # never expose results outside the assignment/scope
    try:
        names = sc.get_many([r["student_id"] for r in rows][:200])
    except StudentServiceError:
        names = {}
    marks = [r["obtained_marks"] for r in rows]
    out_rows = [{**r, "rank": i + 1, "name": (f"{names[r['student_id']].first_name} {names[r['student_id']].last_name}" if r["student_id"] in names else None)}
                for i, r in enumerate(rows)]
    return {
        "exam_id": str(exam_id), "window_closed": utcnow() >= ev.end_at, "total_marks": str(ev.total_marks),
        "summary": {"assigned": len(allowed), "submitted": len(rows), "not_submitted": max(len(allowed) - len(rows), 0),
                    "average": round(sum(marks) / len(marks), 2) if marks else None,
                    "highest": max(marks) if marks else None, "lowest": min(marks) if marks else None},
        "results": out_rows,
    }


@router.post("/exams/{exam_id}/sync")
def sync_exam_assignments(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                          ac: AttemptClient = Depends(get_attempt_client)):
    """Refresh STARTED / COMPLETED / MISSED from the students' real attempts (the worker also does this every minute)."""
    e = _exam(db, exam_id)
    p.require(db, "exam.generate", provider_id=e.provider_id, branch_id=e.branch_id)
    try:
        counts = assignment_sync.sync_exam(db, exam_id, ac)
    except InternalServiceError:
        db.rollback()
        raise api_error(503, "STUDENT_SERVICE_UNAVAILABLE", "The student attempt service is unavailable. Please retry.")
    audit.record(db, "exam.sync", "SUCCESS", actor=p.staff_id, target_type="exam", target_id=exam_id, provider_id=e.provider_id,
                 branch_id=e.branch_id, ip=p.ip, details=counts)
    return counts
