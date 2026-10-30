"""Exam plans, generation, exams, composite papers and assignments."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.core.taxonomy import Label
from institute_common.core.class_levels import TargetClass, normalize_class
from institute_common.models.exams import (
    Exam, ExamGenerationEvent, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanStudent, ExamPlanSubject,
    ExamVersion, ExamVersionPaper, GlobalPaperSubmission, GlobalTest, StudentExamAssignment,
)
from institute_common.models.org import Batch, Branch, Enrollment
from institute_common.models.papers import Paper, PaperVersion
from institute_common.util import need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from institute_common.core.roles import Role
from inst_exam.services import exam_generation as gen
from inst_exam.services import assignment_sync, teacher_scope, unified
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
    plan_id: UUID | None             # None only in a provider's view of a unified Global Test exam: the plan and its anchor
    provider_id: UUID | None         # provider/branch belong to the platform side and may name another provider
    branch_id: UUID | None
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
    unified_global: bool = False      # one exam per student for a Global Test date, assembled from several providers' papers
    visible_subject_ids: list[UUID] | None = None   # a provider's view of a unified exam: only these subjects (None = all)


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    assignment_id: UUID
    student_id: str
    batch_id: UUID | None
    status: str
    name: str | None = None          # from the Student service (None when it is unreachable)


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
    return _plan_outs(db, [plan])[0]


def _plan_outs(db: Session, plans: list[ExamPlan]) -> list[PlanOut]:
    """PlanOut for each plan: batches, listed students, slots and Global Test names in one statement each for the whole page."""
    ids = [x.plan_id for x in plans]
    if not ids:
        return []
    batches: dict[UUID, list] = {}
    for pid, b in db.execute(select(ExamPlanBatch.plan_id, ExamPlanBatch.batch_id).where(ExamPlanBatch.plan_id.in_(ids))):
        batches.setdefault(pid, []).append(b)
    students: dict[UUID, list] = {}
    for pid, sid in db.execute(select(ExamPlanStudent.plan_id, ExamPlanStudent.student_id).where(ExamPlanStudent.plan_id.in_(ids))
                               .order_by(ExamPlanStudent.student_id)):
        students.setdefault(pid, []).append(sid)
    slots: dict[UUID, list] = {}
    for sl in db.scalars(select(ExamPlanSubject).where(ExamPlanSubject.plan_id.in_(ids)).order_by(ExamPlanSubject.position)):
        slots.setdefault(sl.plan_id, []).append(sl)
    gt_ids = {x.global_test_id for x in plans if x.global_test_id}
    gt_names = dict(db.execute(select(GlobalTest.test_id, GlobalTest.name).where(GlobalTest.test_id.in_(gt_ids))).all()) if gt_ids else {}
    return [PlanOut(**{c: getattr(plan, c) for c in ("plan_id", "provider_id", "branch_id", "name", "description", "duration_minutes",
                                                      "start_at", "end_at", "generate_at", "auto_publish", "status", "audience_type",
                                                      "marking_mode", "marks_correct", "marks_wrong", "exam_type", "target_class", "global_test_id")},
                    batch_ids=batches.get(plan.plan_id, []), student_ids=students.get(plan.plan_id, []),
                    global_test_name=gt_names.get(plan.global_test_id),
                    slots=[SlotIn(subject_id=s.subject_id, paper_id=s.paper_id, paper_version_id=s.paper_version_id) for s in slots.get(plan.plan_id, [])])
            for plan in plans]


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
    stmt = select(ExamPlan).where(ExamPlan.branch_id == branch_id)
    if not unified.is_platform(p, "examplan.manage"):        # a unified plan is never listed under its anchor branch
        stmt = stmt.where(~unified.unified_plan_clause(ExamPlan.plan_id))
    res = paginate(db, stmt.order_by(ExamPlan.start_at.desc()), page, page_size)
    res["items"] = _plan_outs(db, list(res["items"]))
    return res


@router.get("/exam-plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    _require_plan(db, p, "examplan.manage", plan)
    return _plan_out(db, plan)


@router.patch("/exam-plans/{plan_id}", response_model=PlanOut)
def update_plan(plan_id: UUID, body: PlanPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Edits affect only FUTURE generations; exams already generated keep their immutable manifest."""
    plan = _plan(db, plan_id)
    _require_plan(db, p, "examplan.manage", plan)
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
    _require_plan(db, p, "examplan.manage", plan)
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
    _require_plan(db, p, "exam.generate", plan)
    start, end = body.start_at or plan.start_at, body.end_at or plan.end_at
    if end <= start:
        raise api_error(422, "INVALID_WINDOW", "end_at must be after start_at.")
    if plan.global_occurrence_id and (start != plan.start_at or end != plan.end_at):
        # a Global Test date has ONE exam in its scheduled window: another window would be a second exam for the same students
        raise api_error(409, "GLOBAL_TEST_WINDOW_FIXED", "A Global Test date is generated only in its scheduled window.")
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
    _require_plan(db, p, "exam.generate", plan)
    return paginate(db, select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan_id).order_by(ExamGenerationJob.started_at.desc()), page, page_size)


@router.get("/jobs/{job_id}/events")
def job_events(job_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    job = db.get(ExamGenerationJob, job_id)
    if not job:
        raise api_error(404, "NOT_FOUND", "Job not found.")
    plan = _plan(db, job.plan_id)
    _require_plan(db, p, "exam.generate", plan)
    return [{"type": e.event_type, "details": e.details, "at": e.occurred_at.isoformat()}
            for e in db.scalars(select(ExamGenerationEvent).where(ExamGenerationEvent.job_id == job_id).order_by(ExamGenerationEvent.occurred_at))]


# ------------------------------------------------------------------ exams

def _platform_only(db: Session, p: Principal, perm: str, target: str, target_id: UUID) -> None:
    if not unified.is_platform(p, perm):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": perm, target: str(target_id), "unified": True})
        raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")


def _require_plan(db: Session, p: Principal, perm: str, plan: ExamPlan) -> None:
    """A unified Global Test plan belongs to the platform; its anchor provider/branch grant nothing (services/unified.py)."""
    if unified.is_unified_plan(db, plan.plan_id):
        _platform_only(db, p, perm, "plan_id", plan.plan_id)
    else:
        p.require(db, perm, provider_id=plan.provider_id, branch_id=plan.branch_id)


def _require_exam(db: Session, p: Principal, perm: str, e: Exam) -> None:
    """Control actions on an exam (publish, cancel, sync): platform-only for a unified Global Test exam."""
    if unified.is_unified_exam(db, e.exam_id):
        _platform_only(db, p, perm, "exam_id", e.exam_id)
    else:
        p.require(db, perm, provider_id=e.provider_id, branch_id=e.branch_id)


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
    branches whose enrolled students they may see (branch admins of an institute-wide exam), or OnlyStudents (teachers). A unified
    Global Test exam returns None for platform roles and otherwise a SubjectScope: the caller's own contributed subjects and students
    (services/unified.py); its anchor provider/branch grant nothing."""
    if unified.is_unified_exam(db, e.exam_id):
        s = unified.scope(db, p, perm, e)
        if s is not None and not s.ids:
            audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": perm, "exam_id": str(e.exam_id), "unified": True})
            raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
        return s
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


def _in_branches(scope: set[UUID] | OnlyStudents | unified.SubjectScope):
    if isinstance(scope, (OnlyStudents, unified.SubjectScope)):
        return StudentExamAssignment.student_id.in_(scope.ids or {""})
    return StudentExamAssignment.enrollment_id.in_(select(Enrollment.enrollment_id).where(Enrollment.branch_id.in_(scope)))


def _exam_out(db: Session, exam: Exam, branches: set[UUID] | None = None) -> ExamOut:
    return _exam_outs(db, [(exam, branches)])[0]


def _prefetch(db: Session, exams) -> None:
    """Load, in two statements, what exam_access/_exam_outs ask per exam: the plans (then served by the session's identity map) and
    which exams are unified (cached for this request)."""
    exams = list(exams)
    plan_ids = {e.plan_id for e in exams if e.plan_id}
    if plan_ids:
        db.scalars(select(ExamPlan).where(ExamPlan.plan_id.in_(plan_ids))).all()
    unified.prefetch_unified_exams(db, [e.exam_id for e in exams])


def _exam_outs(db: Session, rows: list[tuple[Exam, object]]) -> list[ExamOut]:
    """ExamOut for each (exam, caller's scope), loading versions, papers, deliveries and whole-exam assignment counts for all of them in
    one statement each (a list page used to run ~7 statements per exam). Scoped counts stay per exam (they depend on the scope)."""
    if not rows:
        return []
    _prefetch(db, [e for e, _s in rows])
    ids = [e.exam_id for e, _s in rows]
    latest = (select(ExamVersion.exam_id, func.max(ExamVersion.version_no).label("v")).where(ExamVersion.exam_id.in_(ids))
              .group_by(ExamVersion.exam_id).subquery())
    versions = {ev.exam_id: ev for ev in db.scalars(select(ExamVersion).join(latest, (latest.c.exam_id == ExamVersion.exam_id)
                                                                              & (latest.c.v == ExamVersion.version_no)))}
    vids = [ev.exam_version_id for ev in versions.values()]
    papers: dict[UUID, list[ExamVersionPaper]] = {}
    outboxes: dict[UUID, ExamOutbox] = {}
    whole: dict[UUID, dict] = {}
    if vids:
        for x in db.scalars(select(ExamVersionPaper).where(ExamVersionPaper.exam_version_id.in_(vids)).order_by(ExamVersionPaper.position)):
            papers.setdefault(x.exam_version_id, []).append(x)
        for ob in db.scalars(select(ExamOutbox).where(ExamOutbox.exam_version_id.in_(vids), ExamOutbox.operation == "PUBLISH")):
            outboxes.setdefault(ob.exam_version_id, ob)
        unscoped = [versions[e.exam_id].exam_version_id for e, sc in rows if sc is None and e.exam_id in versions]
        if unscoped:
            for vid, status, n in db.execute(select(StudentExamAssignment.exam_version_id, StudentExamAssignment.status, func.count())
                                             .where(StudentExamAssignment.exam_version_id.in_(unscoped))
                                             .group_by(StudentExamAssignment.exam_version_id, StudentExamAssignment.status)):
                whole.setdefault(vid, {})[status] = n
    return [_build_exam_out(db, exam, branches, versions.get(exam.exam_id), papers, outboxes, whole) for exam, branches in rows]


def _build_exam_out(db: Session, exam: Exam, branches, ev: ExamVersion | None, papers_of: dict, outboxes: dict, whole: dict) -> ExamOut:
    out = ExamOut(exam_id=exam.exam_id, plan_id=exam.plan_id, provider_id=exam.provider_id, branch_id=exam.branch_id,
                  name=exam.name, status=exam.status, institute_wide=is_institute_wide(db, exam), unified_global=unified.is_unified_exam(db, exam.exam_id))
    if ev:
        papers = papers_of.get(ev.exam_version_id, [])
        subject_view = isinstance(branches, unified.SubjectScope)
        if subject_view:       # a provider's view of a unified exam: never another provider's subject, nor the whole exam's totals
            papers = [x for x in papers if x.subject_id in branches.subject_ids]
        ob = outboxes.get(ev.exam_version_id)
        out.exam_version_id, out.start_at, out.end_at = ev.exam_version_id, ev.start_at, ev.end_at
        out.duration_minutes, out.total_questions, out.total_marks = ev.duration_minutes, ev.total_questions, ev.total_marks
        out.missing_subjects = ev.missing_subjects or []
        if subject_view:
            out.total_questions, out.total_marks = sum(x.question_count for x in papers), _papers_total(ev, papers)
            out.missing_subjects = [m for m in out.missing_subjects if UUID(m["subject_id"]) in branches.subject_ids]
            out.visible_subject_ids = sorted(branches.subject_ids, key=str)
        # Manifest shows provenance (source provider, version) but never question content.
        out.papers = [{"subject_id": str(x.subject_id), "paper_version_id": str(x.paper_version_id),
                       "source_provider_id": str(x.source_provider_id) if x.source_provider_id else None,
                       "platform_paper": x.source_provider_id is None, "question_count": x.question_count} for x in papers]
        if branches is None:
            out.assignment_status = dict(whole.get(ev.exam_version_id, {}))
        else:
            counts = (select(StudentExamAssignment.status, func.count()).where(StudentExamAssignment.exam_version_id == ev.exam_version_id)
                      .where(_in_branches(branches)))
            out.assignment_status = {k: v for k, v in db.execute(counts.group_by(StudentExamAssignment.status)).all()}
        out.assigned_students = sum(out.assignment_status.values())
        out.settings = ev.settings
        out.delivery_status = ob.status if ob else "NOT_PUBLISHED"
    if isinstance(branches, unified.SubjectScope):
        # The anchor (plan, provider, branch) may be another provider's; whole-exam counts cover every provider's students.
        out.plan_id = out.provider_id = out.branch_id = None
        if out.settings:
            out.settings = {k: v for k, v in out.settings.items() if k != "skipped_duplicates"}
    return out


@router.get("/branches/{branch_id}/exams", response_model=Page[ExamOut])
def list_exams(branch_id: UUID, status: str | None = None, q: str | None = Query(default=None, max_length=100), page: int = 1, page_size: int = 25,
               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    br = _branch(db, branch_id)
    term = (q or "").strip().lower()
    named = (lambda stmt: stmt.where(func.lower(Exam.name).contains(term))) if term else (lambda stmt: stmt)   # optional name search
    p.require(db, "exam.read", provider_id=br.provider_id, branch_id=branch_id)
    if not p.can("exam.read", exclude_roles=(Role.TEACHER,), provider_id=br.provider_id, branch_id=branch_id):
        # teachers: the provider's exams with a targeted student they teach in this branch (wherever the exam was planned)
        stmt = named(select(Exam).where(Exam.provider_id == br.provider_id, ~unified.unified_exam_clause(Exam.exam_id)))
        if status in {"SCHEDULED", "PUBLISHED", "CANCELLED"}:
            stmt = stmt.where(Exam.status == status)
        candidates = db.scalars(stmt.order_by(Exam.created_at.desc())).all()
        _prefetch(db, candidates)
        mine = [(e, exam_access(db, p, "exam.read", e)) for e in candidates if teacher_scope.exam_students(db, p, e, branch_id)]
        extra = [x for x in _unified_in_branch(db, p, br, status) if term in x[0].name.lower()]
        return _page_of(db, sorted(mine + extra, key=lambda x: x[0].created_at, reverse=True), page, page_size)
    # + whole-institute exams of the provider planned in another branch (one exam, never copied per branch)
    institute = select(ExamPlan.plan_id).where(ExamPlan.provider_id == br.provider_id, ExamPlan.audience_type == "INSTITUTE")
    stmt = named(select(Exam).where((Exam.branch_id == branch_id) | ((Exam.provider_id == br.provider_id) & Exam.plan_id.in_(institute)),
                                    ~unified.unified_exam_clause(Exam.exam_id)))
    if status in {"SCHEDULED", "PUBLISHED", "CANCELLED"}:
        stmt = stmt.where(Exam.status == status)
    extra = [x for x in _unified_in_branch(db, p, br, status) if term in x[0].name.lower()]
    if extra:
        exams = db.scalars(stmt).all()
        _prefetch(db, exams)
        rows = [(e, exam_access(db, p, "exam.read", e)) for e in exams] + extra
        return _page_of(db, sorted(rows, key=lambda x: x[0].created_at, reverse=True), page, page_size)
    res = paginate(db, stmt.order_by(Exam.created_at.desc()), page, page_size)
    _prefetch(db, res["items"])
    res["items"] = _exam_outs(db, [(e, exam_access(db, p, "exam.read", e)) for e in res["items"]])
    return res


def _page_of(db: Session, rows: list[tuple[Exam, object]], page: int, page_size: int) -> dict:
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    return {"items": _exam_outs(db, rows[(page - 1) * page_size: page * page_size]),
            "total": len(rows), "page": page, "page_size": page_size}


def _unified_in_branch(db: Session, p: Principal, br: Branch, status: str | None) -> list[tuple[Exam, object]]:
    """Unified Global Test exams in which this branch's provider supplied a paper, with the caller's view narrowed to the assigned
    students enrolled with that provider in this branch. Platform roles see the whole exam; others only their SubjectScope."""
    contributed = (select(ExamVersion.exam_id).join(ExamVersionPaper, ExamVersionPaper.exam_version_id == ExamVersion.exam_version_id)
                   .join(GlobalPaperSubmission, GlobalPaperSubmission.submission_id == ExamVersionPaper.source_submission_id)
                   .where(GlobalPaperSubmission.provider_id == br.provider_id))
    stmt = select(Exam).where(Exam.exam_id.in_(contributed))
    if status in {"SCHEDULED", "PUBLISHED", "CANCELLED"}:
        stmt = stmt.where(Exam.status == status)
    out = []
    exams = db.scalars(stmt).all()
    _prefetch(db, exams)
    here_by_exam: dict[UUID, set[str]] = {}
    for exam_id, sid in db.execute(select(StudentExamAssignment.exam_id, StudentExamAssignment.student_id)
                                   .join(Enrollment, Enrollment.student_id == StudentExamAssignment.student_id)
                                   .where(StudentExamAssignment.exam_id.in_([e.exam_id for e in exams] or [None]),
                                          Enrollment.provider_id == br.provider_id, Enrollment.branch_id == br.branch_id)):
        here_by_exam.setdefault(exam_id, set()).add(sid)
    for e in exams:
        here = here_by_exam.get(e.exam_id, set())
        s = unified.scope(db, p, "exam.read", e)
        if s is None:
            if here:
                out.append((e, None))
        elif s.ids & here:
            out.append((e, unified.SubjectScope(frozenset(s.ids & here), s.subject_ids)))
    return out


@router.get("/exams/{exam_id}", response_model=ExamOut)
def get_exam(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _exam(db, exam_id)
    return _exam_out(db, e, exam_access(db, p, "exam.read", e))


@router.post("/exams/{exam_id}/publish", response_model=ExamOut)
def publish_exam(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _exam(db, exam_id)
    _require_exam(db, p, "exam.publish", e)
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
    _require_exam(db, p, "exam.schedule", e)
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
def exam_assignments(exam_id: UUID, page: int = 1, page_size: int = 50, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                     sc: StudentClient = Depends(get_student_client)):
    """The students an exam is assigned to, limited to those the caller may see (the same scope as the exam's assigned count)."""
    e = _exam(db, exam_id)
    branches = exam_access(db, p, "exam.read", e)
    stmt = select(StudentExamAssignment).where(StudentExamAssignment.exam_id == exam_id)
    if branches is not None:
        stmt = stmt.where(_in_branches(branches))
    res = paginate(db, stmt.order_by(StudentExamAssignment.student_id), page, page_size)
    try:
        names = sc.get_many([a.student_id for a in res["items"]])
    except StudentServiceError:
        names = {}
    res["items"] = [AssignmentOut.model_validate(a).model_copy(update={"name": f"{names[a.student_id].first_name} {names[a.student_id].last_name}".strip()
                                                                       if a.student_id in names else None}) for a in res["items"]]
    return res


# ------------------------------------------------------------------ results & analytics

def _papers_total(ev: ExamVersion, papers) -> Decimal:
    """Marks of some of an exam's papers as delivered: UNIFORM marking gives every question the exam-level marks (as the generator's
    exam total does); otherwise each paper keeps its own total."""
    marking = (ev.settings or {}).get("marking") or {}
    if marking.get("mode") == "UNIFORM":
        total = Decimal(marking["correct"]) * sum(x.question_count for x in papers)
    else:
        total = sum((x.total_marks for x in papers), Decimal(0))
    return total.quantize(Decimal("0.01"))            # the same 2-decimal form as exam_versions.total_marks


def _subject_rows(db: Session, rc: ResultClient, ev: ExamVersion, scope: unified.SubjectScope) -> list[dict]:
    """A provider's result rows of a unified exam: only its own subjects' marks, summed over those subjects (never the overall)."""
    names = set(db.scalars(select(Subject.name).where(Subject.subject_id.in_(scope.subject_ids or [None]))))
    out = []
    for r in rc.exam_subject_results(str(ev.exam_version_id)):
        # by paper when the attempt reports its papers (students who chose papers: another provider may contribute a paper of the
        # same subject); otherwise by subject, which within one exam is one paper (every exam made for one combination of papers)
        by_paper = any(x.get("paper_id") for x in r["subjects"])
        mine = [x for x in r["subjects"] if (x.get("paper_id") in scope.paper_ids if by_paper else x["subject"] in names)]
        if by_paper and not mine:
            continue                              # this student sat none of the caller's papers
        add = lambda k, mine=mine: sum(x[k] for x in mine)  # noqa: E731
        attempted, correct = add("attempted_questions"), add("correct_answers")
        out.append({"student_id": r["student_id"], "subjects": mine, "total_marks": add("total_marks"), "obtained_marks": add("obtained_marks"),
                    "attempted_questions": attempted, "correct_answers": correct, "incorrect_answers": add("incorrect_answers"),
                    "unanswered_questions": add("unanswered_questions"), "accuracy": round(correct / attempted * 100, 2) if attempted else 0.0,
                    "submitted_at": r["submitted_at"]})
    return sorted(out, key=lambda x: -x["obtained_marks"])


def competition_ranked(rows: list[dict]) -> list[dict]:
    """Global Test ranking, the same rule as the student ranking service: obtained / that student's own maximum compared as exact
    fractions, equal ratios share a rank (1, 1, 3), ordered inside a rank by raw marks; a zero maximum is not ranked (listed last)."""
    ranked = sorted((r for r in rows if r["total_marks"]), key=lambda r: (-Fraction(r["obtained_marks"], r["total_marks"]), -r["obtained_marks"]))
    out, previous, current = [], None, 0
    for i, r in enumerate(ranked, start=1):
        ratio = Fraction(r["obtained_marks"], r["total_marks"])
        if ratio != previous:
            current, previous = i, ratio
        out.append({**r, "rank": current})
    return out + [{**r, "rank": None} for r in rows if not r["total_marks"]]


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
    subject_view = isinstance(branches, unified.SubjectScope)
    try:
        rows = _subject_rows(db, rc, ev, branches) if subject_view else rc.exam_results(str(ev.exam_version_id))
        if not subject_view:                    # the subject-wise breakdown of each official result (the provider view already has its own)
            by_student = {r["student_id"]: r["subjects"] for r in rc.exam_subject_results(str(ev.exam_version_id))}
            rows = [{**r, "subjects": by_student.get(r["student_id"], [])} for r in rows]
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
    unified_exam = unified.is_unified_exam(db, e.exam_id)
    if unified_exam:
        rows = competition_ranked(rows)           # Global Test: exact obtained/maximum ratio, shared ranks (as the student ranking)
    out_rows = [{**r, "rank": r["rank"] if unified_exam else i + 1,
                 "name": (f"{names[r['student_id']].first_name} {names[r['student_id']].last_name}" if r["student_id"] in names else None)}
                for i, r in enumerate(rows)]
    if subject_view:
        visible = [x for x in db.scalars(select(ExamVersionPaper).where(ExamVersionPaper.exam_version_id == ev.exam_version_id))
                   if str(x.source_submission_id) in branches.paper_ids]
        total = _papers_total(ev, visible)
    return {
        "exam_id": str(exam_id), "window_closed": utcnow() >= ev.end_at, "total_marks": str(total if subject_view else ev.total_marks),
        "global_test": unified_exam,                              # contributors may correct their own papers' answer keys
        "subject_view": subject_view, "visible_subjects": sorted({s["subject"] for r in rows for s in r.get("subjects", [])}) if subject_view else None,
        "summary": {"assigned": len(allowed), "submitted": len(rows), "not_submitted": max(len(allowed) - len(rows), 0),
                    "average": round(sum(marks) / len(marks), 2) if marks else None,
                    "highest": max(marks) if marks else None, "lowest": min(marks) if marks else None},
        "results": out_rows,
    }


class RequeueIn(Strict):
    reason: str = Field(max_length=1000)


@router.post("/exams/{exam_id}/outbox/requeue")
def requeue_delivery(exam_id: UUID, body: RequeueIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                     p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Super Admin only, after investigating a FAILED delivery: send it again. The same outbox row is reset (same Idempotency-Key towards
    the Student services, which acknowledge an exam they already hold), so nothing is ever delivered or created twice. Repeating the
    request when nothing has failed changes nothing."""
    e = _exam(db, exam_id)
    if not p.has_role(Role.SUPER_ADMIN):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "outbox.requeue", "exam_id": str(exam_id)})
        raise api_error(403, "FORBIDDEN", "Only the Super Admin can send a failed delivery again.")
    if not idempotency_key or not 8 <= len(idempotency_key.strip()) <= 100:
        raise api_error(422, "IDEMPOTENCY_KEY_REQUIRED", "An Idempotency-Key header of 8-100 characters is required.")
    reason = need_reason(body.reason)
    rows = db.scalars(select(ExamOutbox).join(ExamVersion, ExamVersion.exam_version_id == ExamOutbox.exam_version_id)
                      .where(ExamVersion.exam_id == exam_id)).all()
    requeued = [r for r in rows if r.status == "FAILED"]
    for r in requeued:
        r.status, r.attempts, r.next_attempt_at, r.last_error = "PENDING", 0, utcnow(), None
    db.commit()
    if requeued:
        audit.record(db, "exam.outbox.requeue", "SUCCESS", actor=p.staff_id, target_type="exam", target_id=exam_id, provider_id=e.provider_id,
                     branch_id=e.branch_id, reason=reason, ip=p.ip, details={"operations": [r.operation for r in requeued]})
    return {"exam_id": str(exam_id), "requeued": [r.operation for r in requeued],
            "deliveries": [{"operation": r.operation, "status": r.status} for r in rows]}


@router.post("/exams/{exam_id}/sync")
def sync_exam_assignments(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                          ac: AttemptClient = Depends(get_attempt_client)):
    """Refresh STARTED / COMPLETED / MISSED from the students' real attempts (the worker also does this every minute)."""
    e = _exam(db, exam_id)
    _require_exam(db, p, "exam.generate", e)
    try:
        counts = assignment_sync.sync_exam(db, exam_id, ac)
    except InternalServiceError:
        db.rollback()
        raise api_error(503, "STUDENT_SERVICE_UNAVAILABLE", "The student attempt service is unavailable. Please retry.")
    audit.record(db, "exam.sync", "SUCCESS", actor=p.staff_id, target_type="exam", target_id=exam_id, provider_id=e.provider_id,
                 branch_id=e.branch_id, ip=p.ip, details=counts)
    return counts
