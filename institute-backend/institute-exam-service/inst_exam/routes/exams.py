"""Exam plans, generation, exams, composite papers and assignments."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject
from institute_common.core.taxonomy import Label
from institute_common.core.class_levels import TargetClass, normalize_class
from institute_common.models.exams import (
    Exam, ExamGenerationEvent, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanStudent, ExamPlanSubject,
    ExamVersion, ExamVersionPaper, GlobalTest, StudentExamAssignment,
)
from institute_common.models.org import Batch, Branch, Enrollment
from institute_common.models.papers import Paper, PaperVersion
from institute_common.util import need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from institute_common.core.roles import Role
from inst_exam.services import exam_generation as gen
from inst_exam.services import assignment_sync, teacher_scope
from institute_common.clients.attempt_client import AttemptClient, get_attempt_client
from institute_common.internal_http import InternalServiceError
from institute_common.clients.result_client import ResultClient, ResultServiceError, get_result_client
from institute_common.clients.student_client import StudentClient, StudentServiceError, get_student_client

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


AudienceType = Literal["BATCHES", "BRANCH", "INSTITUTE", "STUDENTS"]


class AudienceIn(Strict):
    """Who sits the exam. BATCHES needs batch_ids, STUDENTS needs student_ids (global Student IDs); BRANCH and INSTITUTE need neither."""
    audience_type: AudienceType = "BATCHES"
    batch_ids: list[UUID] = Field(default_factory=list, max_length=50)
    student_ids: list[str] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def audience_rule(self):
        if self.audience_type == "BATCHES" and not self.batch_ids:
            raise ValueError("choose at least one batch")
        if self.audience_type == "STUDENTS" and not self.student_ids:
            raise ValueError("choose at least one student")
        if self.audience_type != "BATCHES" and self.batch_ids:
            raise ValueError("batch_ids apply only to a BATCHES audience")
        if self.audience_type != "STUDENTS" and self.student_ids:
            raise ValueError("student_ids apply only to a STUDENTS audience")
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("a student is listed twice")
        return self


class MarkingIn(Strict):
    """PER_QUESTION (default): each question's own marks. UNIFORM: +marks_correct for a right answer and -marks_wrong for a wrong
    one, for every question of the exam (whole numbers, as the Student services require). Unanswered questions always score 0."""
    marking_mode: Literal["PER_QUESTION", "UNIFORM"] = "PER_QUESTION"
    marks_correct: int | None = Field(default=None, ge=1, le=100)
    marks_wrong: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def marking_rule(self):
        if self.marking_mode == "UNIFORM" and (self.marks_correct is None or self.marks_wrong is None):
            raise ValueError("uniform marking needs marks_correct and marks_wrong")
        if self.marking_mode == "PER_QUESTION" and (self.marks_correct is not None or self.marks_wrong is not None):
            raise ValueError("marks_correct/marks_wrong apply only to UNIFORM marking")
        return self


class PlanIn(AudienceIn, MarkingIn):
    name: str = Field(min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int = Field(gt=0, le=720)
    start_at: datetime
    end_at: datetime
    generate_at: datetime | None = None
    auto_publish: bool = True
    status: Literal["ACTIVE", "DRAFT"] = "ACTIVE"      # DRAFT: saved but never generated until activated
    exam_type: Label = None
    target_class: TargetClass = None
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
    exam_type: Label = None
    target_class: TargetClass = None
    marking_mode: Literal["PER_QUESTION", "UNIFORM"] | None = None
    marks_correct: int | None = Field(default=None, ge=1, le=100)
    marks_wrong: int | None = Field(default=None, ge=0, le=100)


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
    audience_type: str = "BATCHES"
    batch_ids: list[UUID]
    student_ids: list[str] = []
    marking_mode: str = "PER_QUESTION"
    marks_correct: int | None = None
    marks_wrong: int | None = None
    exam_type: str | None = None
    target_class: str | None = None
    global_test_id: UUID | None = None
    global_test_name: str | None = None
    slots: list[SlotIn]


class PreviewOut(BaseModel):
    eligible_count: int
    sample_student_ids: list[str]
    not_eligible_student_ids: list[str] = []
    # Students the plan's Class kept out: no Class set on their enrollment / a different Class
    excluded_no_class: int = 0
    excluded_other_class: int = 0


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
    assignment_status: dict[str, int] = {}
    settings: dict | None = None
    delivery_status: str | None = None
    institute_wide: bool = False      # whole-institute exam: visible to every branch admin of the provider (own students only)


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
    students = [sid for (sid,) in db.execute(select(ExamPlanStudent.student_id).where(ExamPlanStudent.plan_id == plan.plan_id).order_by(ExamPlanStudent.student_id))]
    slots = db.scalars(select(ExamPlanSubject).where(ExamPlanSubject.plan_id == plan.plan_id).order_by(ExamPlanSubject.position)).all()
    gt = db.get(GlobalTest, plan.global_test_id) if plan.global_test_id else None
    return PlanOut(**{c: getattr(plan, c) for c in ("plan_id", "provider_id", "branch_id", "name", "description", "duration_minutes",
                                                     "start_at", "end_at", "generate_at", "auto_publish", "status", "audience_type",
                                                     "marking_mode", "marks_correct", "marks_wrong", "exam_type", "target_class", "global_test_id")},
                   batch_ids=batches, student_ids=students, global_test_name=gt.name if gt else None,
                   slots=[SlotIn(subject_id=s.subject_id, paper_id=s.paper_id, paper_version_id=s.paper_version_id) for s in slots])


def require_plan_scope(db: Session, p: Principal, br: Branch, audience_type: str) -> None:
    """examplan.manage on the branch; a whole-institute audience additionally needs it for the whole provider."""
    p.require(db, "examplan.manage", provider_id=br.provider_id, branch_id=br.branch_id)
    if audience_type == "INSTITUTE":
        p.require(db, "examplan.manage", provider_id=br.provider_id)
        if not p.can("examplan.manage", provider_id=br.provider_id):
            raise api_error(403, "FORBIDDEN", "Only provider-level staff can assign a test to the whole institute.")


def validate_audience(db: Session, br: Branch, audience: AudienceIn) -> list[str]:
    """Server-side eligibility of the requested audience. Returns listed students WITHOUT an active enrollment at the provider."""
    for bid in dict.fromkeys(audience.batch_ids):
        b = db.get(Batch, bid)
        if not b or b.branch_id != br.branch_id or b.status != "ACTIVE":
            raise api_error(422, "INVALID_BATCH", "A batch does not belong to this branch or is inactive.")
    if audience.audience_type != "STUDENTS":
        return []
    enrolled = {sid for (sid,) in db.execute(select(Enrollment.student_id).where(
        Enrollment.provider_id == br.provider_id, Enrollment.status == "ACTIVE", Enrollment.student_id.in_(audience.student_ids)))}
    return [sid for sid in audience.student_ids if sid not in enrolled]


def write_audience(db: Session, plan: ExamPlan, audience: AudienceIn) -> None:
    for old in db.scalars(select(ExamPlanBatch).where(ExamPlanBatch.plan_id == plan.plan_id)):
        db.delete(old)
    for old in db.scalars(select(ExamPlanStudent).where(ExamPlanStudent.plan_id == plan.plan_id)):
        db.delete(old)
    db.flush()
    plan.audience_type = audience.audience_type
    for bid in dict.fromkeys(audience.batch_ids):
        db.add(ExamPlanBatch(plan_id=plan.plan_id, batch_id=bid))
    for sid in audience.student_ids:
        db.add(ExamPlanStudent(plan_id=plan.plan_id, student_id=sid))


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
        version_id = s.paper_version_id
        if version_id is None and paper.scope == "GLOBAL":
            version_id = db.scalar(select(PaperVersion.paper_version_id).where(PaperVersion.paper_id == paper.paper_id, PaperVersion.status == "PUBLISHED")
                                   .order_by(PaperVersion.version_no.desc()).limit(1))
        if not gen.may_use_platform_paper(db, None, paper, version_id):
            raise api_error(422, "GLOBAL_TEST_PAPER", "This platform paper belongs to a Global Test: schedule that test instead of reusing its paper.")


def _write_slots(db: Session, plan: ExamPlan, slots: list[SlotIn]) -> None:
    for pos, s in enumerate(slots, start=1):
        db.add(ExamPlanSubject(plan_id=plan.plan_id, position=pos, subject_id=s.subject_id, paper_id=s.paper_id,
                               paper_version_id=s.paper_version_id))


@router.post("/branches/{branch_id}/exam-plans", response_model=PlanOut, status_code=201)
def create_plan(branch_id: UUID, body: PlanIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    require_plan_scope(db, p, br, body.audience_type)
    _validate_slots(db, br.provider_id, body.slots)
    missing = validate_audience(db, br, body)
    if missing:
        raise api_error_detail(422, "STUDENTS_NOT_ENROLLED", "Some students are not actively enrolled with this provider.", student_ids=missing[:100])
    plan = ExamPlan(provider_id=br.provider_id, branch_id=branch_id, created_by=p.staff_id,
                    **body.model_dump(exclude={"batch_ids", "student_ids", "slots", "audience_type"}))
    db.add(plan)
    db.flush()
    write_audience(db, plan, body)
    _write_slots(db, plan, body.slots)
    db.commit()
    audit.record(db, "examplan.create", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan.plan_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip,
                 details={"audience": body.audience_type, "marking": body.marking_mode, "status": body.status})
    return _plan_out(db, plan)


class PlanCheckIn(PlanIn):
    """Everything a plan needs, for a dry run before anything else is created. `slots` may be empty and `pending_subject_ids` names
    subjects whose papers do not exist yet (the wizard builds them from bank questions only after this check passes)."""
    slots: list[SlotIn] = Field(default_factory=list, max_length=20)
    pending_subject_ids: list[UUID] = Field(default_factory=list, max_length=20)


@router.post("/branches/{branch_id}/exam-plans/check")
def check_plan(branch_id: UUID, body: PlanCheckIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Validate a plan exactly as create would (scope, window, marking, audience, subjects, papers), writing nothing."""
    br = _branch(db, branch_id)
    require_plan_scope(db, p, br, body.audience_type)
    if not body.slots and not body.pending_subject_ids:
        raise api_error(422, "NO_SUBJECTS", "Add at least one subject.")
    subjects = [s.subject_id for s in body.slots] + list(body.pending_subject_ids)
    if len(set(subjects)) != len(subjects):
        raise api_error(422, "DUPLICATE_SUBJECT", "Each subject may appear once in a plan.")
    _validate_slots(db, br.provider_id, body.slots)
    for sid in body.pending_subject_ids:
        if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == br.provider_id, ProviderSubject.subject_id == sid)):
            raise api_error(422, "SUBJECT_NOT_ENABLED", "A subject in the plan is not enabled for this provider.")
    missing = validate_audience(db, br, body)
    if missing:
        raise api_error_detail(422, "STUDENTS_NOT_ENROLLED", "Some students are not actively enrolled with this provider.", student_ids=missing[:100])
    return {"ok": True}


class AudiencePreviewIn(AudienceIn):
    subject_ids: list[UUID] = Field(default_factory=list, max_length=20)   # the plan's subjects, when already chosen
    target_class: TargetClass = None                                      # the plan's Class, when chosen


@router.post("/branches/{branch_id}/audience-preview", response_model=PreviewOut)
def audience_preview(branch_id: UUID, body: AudiencePreviewIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """How many students the audience reaches right now (the same resolution generation uses)."""
    br = _branch(db, branch_id)
    require_plan_scope(db, p, br, body.audience_type)
    missing = validate_audience(db, br, body)
    report: dict = {}
    rows = gen.resolve_audience(db, br.provider_id, branch_id, body.audience_type, body.batch_ids, body.student_ids,
                                subject_ids=body.subject_ids, class_level=normalize_class(body.target_class), report=report)
    got = {r[0] for r in rows}
    return PreviewOut(eligible_count=len(rows), sample_student_ids=[r[0] for r in rows[:10]], not_eligible_student_ids=missing[:100],
                      excluded_no_class=len(report.get("excluded_no_class", set()) - got),
                      excluded_other_class=len(report.get("excluded_other_class", set()) - got))


@router.put("/exam-plans/{plan_id}/audience", response_model=PlanOut)
def replace_audience(plan_id: UUID, body: AudienceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Change who future generations assign. Exams already generated keep their assignments."""
    plan = _plan(db, plan_id)
    br = _branch(db, plan.branch_id)
    require_plan_scope(db, p, br, body.audience_type)
    missing = validate_audience(db, br, body)
    if missing:
        raise api_error_detail(422, "STUDENTS_NOT_ENROLLED", "Some students are not actively enrolled with this provider.", student_ids=missing[:100])
    write_audience(db, plan, body)
    db.commit()
    audit.record(db, "examplan.audience", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan_id,
                 provider_id=plan.provider_id, branch_id=plan.branch_id, ip=p.ip, details={"audience": body.audience_type})
    return _plan_out(db, plan)


def api_error_detail(status: int, code: str, message: str, **extra):
    from fastapi import HTTPException
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


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
    changes = body.model_dump(exclude_unset=True)
    if plan.global_test_id and {"duration_minutes", "marking_mode", "marks_correct", "marks_wrong"} & changes.keys():
        raise api_error(409, "GLOBAL_TEST_LOCKED", "A Global Test keeps its own duration and marking scheme.")
    if changes.get("status") == "ACTIVE" and plan.global_test_id:
        gt = db.get(GlobalTest, plan.global_test_id)
        if not gt or gt.status != "PUBLISHED":
            raise api_error(409, "GLOBAL_TEST_NOT_PUBLISHED", "This plan's Global Test was withdrawn; it cannot be reactivated.")
    for k, v in changes.items():
        setattr(plan, k, v)
    if plan.end_at <= plan.start_at:
        db.rollback()
        raise api_error(422, "INVALID_WINDOW", "end_at must be after start_at.")
    if plan.marking_mode == "UNIFORM" and (not plan.marks_correct or plan.marks_wrong is None):
        db.rollback()
        raise api_error(422, "INVALID_MARKING", "Uniform marking needs marks for a correct and a wrong answer.")
    if plan.marking_mode == "PER_QUESTION":
        plan.marks_correct = plan.marks_wrong = None
    db.commit()
    audit.record(db, "examplan.update", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan_id,
                 provider_id=plan.provider_id, branch_id=plan.branch_id, ip=p.ip)
    return _plan_out(db, plan)


@router.put("/exam-plans/{plan_id}/slots", response_model=PlanOut)
def replace_slots(plan_id: UUID, slots: list[SlotIn], p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    p.require(db, "examplan.manage", provider_id=plan.provider_id, branch_id=plan.branch_id)
    if plan.global_test_id:
        raise api_error(409, "GLOBAL_TEST_LOCKED", "A Global Test uses exactly its published papers.")
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
    # One exam per plan and window start: manual and scheduled generation converge on the same run (the Idempotency-Key header is
    # still accepted but no longer creates a separate exam for the same window).
    try:
        job = gen.generate(db, plan_id, actor=p.staff_id, start_at=body.start_at, end_at=body.end_at)
    except gen.PlanNotGeneratable as exc:
        raise api_error(409, "PLAN_NOT_GENERATABLE", str(exc))
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


# ------------------------------------------------------------------ exam visibility
# Ordinary exams are branch-scoped. A whole-institute exam (its plan's audience is INSTITUTE) exists once, in the branch it was
# planned in, and is visible provider-wide: platform roles and provider admins see all of it; a branch admin of ANY branch of that
# provider sees the exam, its detail and its results restricted to the students enrolled in their own branches. A teacher (who has
# no admin role for the exam) sees it only when one of its targeted students is in their teaching scope for one of its subjects, and
# then only those students (services/teacher_scope.py).


@dataclass(frozen=True)
class OnlyStudents:
    """Exam scope of a teacher: exactly these students."""
    ids: frozenset[str]

def is_institute_wide(db: Session, e: Exam) -> bool:
    plan = db.get(ExamPlan, e.plan_id)
    return bool(plan and plan.audience_type in ("INSTITUTE", "TARGETED"))   # TARGETED: Create Test, may span the provider's branches


def exam_access(db: Session, p: Principal, perm: str, e: Exam) -> set[UUID] | OnlyStudents | None:
    """Authorise `perm` on exam `e` (403 otherwise). Returns None when the caller may see every assigned student, the set of
    branches whose enrolled students they may see (branch admins of an institute-wide exam), or OnlyStudents (teachers)."""
    if p.can(perm, exclude_roles=(Role.BRANCH_ADMIN, Role.TEACHER), provider_id=e.provider_id):
        return None                                       # platform roles, provider admins
    if is_institute_wide(db, e):
        branches = {a.branch_id for a in p.assignments if Role(a.role) == Role.BRANCH_ADMIN and a.provider_id == e.provider_id
                    and p.can(perm, provider_id=e.provider_id, branch_id=a.branch_id)}
        if branches:
            return branches
    if p.can(perm, exclude_roles=(Role.TEACHER,), provider_id=e.provider_id, branch_id=e.branch_id):
        return {e.branch_id} if is_institute_wide(db, e) else None
    students = teacher_scope.exam_students(db, p, e) if p.has_role(Role.TEACHER) and p.can(perm, provider_id=e.provider_id) else set()
    if not students:
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=e.provider_id, branch_id=e.branch_id,
                     details={"permission": perm, "exam_id": str(e.exam_id)})
        raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
    return OnlyStudents(frozenset(students))


def _in_branches(scope: set[UUID] | OnlyStudents):
    if isinstance(scope, OnlyStudents):
        return StudentExamAssignment.student_id.in_(scope.ids or {""})
    return StudentExamAssignment.enrollment_id.in_(select(Enrollment.enrollment_id).where(Enrollment.branch_id.in_(scope)))


def _exam_out(db: Session, exam: Exam, branches: set[UUID] | None = None) -> ExamOut:
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam.exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    out = ExamOut(exam_id=exam.exam_id, plan_id=exam.plan_id, provider_id=exam.provider_id, branch_id=exam.branch_id,
                  name=exam.name, status=exam.status, institute_wide=is_institute_wide(db, exam))
    if ev:
        papers = db.scalars(select(ExamVersionPaper).where(ExamVersionPaper.exam_version_id == ev.exam_version_id).order_by(ExamVersionPaper.position)).all()
        ob = db.scalar(select(ExamOutbox).where(ExamOutbox.exam_version_id == ev.exam_version_id, ExamOutbox.operation == "PUBLISH"))
        out.exam_version_id, out.start_at, out.end_at = ev.exam_version_id, ev.start_at, ev.end_at
        out.duration_minutes, out.total_questions, out.total_marks = ev.duration_minutes, ev.total_questions, ev.total_marks
        out.missing_subjects = ev.missing_subjects or []
        # Manifest shows provenance (source provider, version) but never question content.
        out.papers = [{"subject_id": str(x.subject_id), "paper_version_id": str(x.paper_version_id),
                       "source_provider_id": str(x.source_provider_id) if x.source_provider_id else None,
                       "platform_paper": x.source_provider_id is None, "question_count": x.question_count} for x in papers]
        counts = select(StudentExamAssignment.status, func.count()).where(StudentExamAssignment.exam_version_id == ev.exam_version_id)
        if branches is not None:
            counts = counts.where(_in_branches(branches))
        out.assignment_status = {k: v for k, v in db.execute(counts.group_by(StudentExamAssignment.status)).all()}
        out.assigned_students = sum(out.assignment_status.values())
        out.settings = ev.settings
        out.delivery_status = ob.status if ob else "NOT_PUBLISHED"
    return out


@router.get("/branches/{branch_id}/exams", response_model=Page[ExamOut])
def list_exams(branch_id: UUID, status: str | None = None, page: int = 1, page_size: int = 25,
               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    p.require(db, "exam.read", provider_id=br.provider_id, branch_id=branch_id)
    if not p.can("exam.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
        # teachers: the provider's exams with a targeted student they teach in this branch (wherever the exam was planned)
        stmt = select(Exam).where(Exam.provider_id == br.provider_id)
        if status in {"SCHEDULED", "PUBLISHED", "CANCELLED"}:
            stmt = stmt.where(Exam.status == status)
        mine = [e for e in db.scalars(stmt.order_by(Exam.created_at.desc())) if teacher_scope.exam_students(db, p, e, branch_id)]
        page, page_size = max(page, 1), min(max(page_size, 1), 100)
        return {"items": [_exam_out(db, e, exam_access(db, p, "exam.read", e)) for e in mine[(page - 1) * page_size: page * page_size]],
                "total": len(mine), "page": page, "page_size": page_size}
    # + whole-institute exams of the provider planned in another branch (one exam, never copied per branch)
    institute = select(ExamPlan.plan_id).where(ExamPlan.provider_id == br.provider_id, ExamPlan.audience_type == "INSTITUTE")
    stmt = select(Exam).where((Exam.branch_id == branch_id) | ((Exam.provider_id == br.provider_id) & Exam.plan_id.in_(institute)))
    if status in {"SCHEDULED", "PUBLISHED", "CANCELLED"}:
        stmt = stmt.where(Exam.status == status)
    res = paginate(db, stmt.order_by(Exam.created_at.desc()), page, page_size)
    res["items"] = [_exam_out(db, e, exam_access(db, p, "exam.read", e)) for e in res["items"]]
    return res


@router.get("/exams/{exam_id}", response_model=ExamOut)
def get_exam(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _exam(db, exam_id)
    return _exam_out(db, e, exam_access(db, p, "exam.read", e))


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
    branches = exam_access(db, p, "exam.read", e)
    stmt = select(StudentExamAssignment).where(StudentExamAssignment.exam_id == exam_id)
    if branches is not None:
        stmt = stmt.where(_in_branches(branches))
    return paginate(db, stmt.order_by(StudentExamAssignment.student_id), page, page_size)


# ------------------------------------------------------------------ results & analytics

@router.get("/exams/{exam_id}/results")
def exam_results(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                 rc: ResultClient = Depends(get_result_client), sc: StudentClient = Depends(get_student_client)):
    """Results are evaluated by the Student services; this reads them through the internal API.
    Teachers see only the targeted students in their teaching scope (see exam_access)."""
    e = _exam(db, exam_id)
    branches = exam_access(db, p, "result.read", e)
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    if e.status != "PUBLISHED" or not ev:
        raise api_error(409, "NOT_PUBLISHED", "Results exist only for published exams.")
    try:
        rows = rc.exam_results(str(ev.exam_version_id))
    except ResultServiceError:
        raise api_error(503, "RESULT_SERVICE_UNAVAILABLE", "Results are temporarily unavailable.")

    scoped = select(StudentExamAssignment).where(StudentExamAssignment.exam_version_id == ev.exam_version_id)
    if branches is not None:
        scoped = scoped.where(_in_branches(branches))         # one branch's students, or a teacher's own students only
    assignments = {a.student_id: a for a in db.scalars(scoped)}
    allowed = set(assignments)
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
