"""Global Tests: tests the platform publishes once and providers schedule for their own students.

Lifecycle DRAFT -> PUBLISHED -> WITHDRAWN, managed only by the platform roles (Super Admin / Admin). A test is a list of sections, each
an exact approved/published version of a GLOBAL (platform) paper. Once published nothing about the test changes: providers schedule it
through an exam plan whose papers, duration and marking are locked to the test (see exams.update_plan / replace_slots), so a
provider's assignment can never modify the published test. A different test is always a new record (`derive`)."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.core.taxonomy import Label
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import ExamPlan, ExamPlanSubject, GlobalTest, GlobalTestSection
from institute_common.models.papers import Paper, PaperVersion
from institute_common.models.staff import Staff
from institute_common.schemas.common import Page
from institute_common.util import Strict, need_reason, paginate
from inst_exam.routes.exams import (
    AudienceIn, PlanOut, _branch, _plan_out, api_error_detail, require_plan_scope, validate_audience, write_audience,
)

router = APIRouter(prefix="/api/v1", tags=["Global Tests"])

USABLE = {"APPROVED", "PUBLISHED"}


class SectionIn(Strict):
    paper_version_id: UUID


class GlobalTestIn(Strict):
    name: str = Field(min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    exam_type: Label = None
    target_class: Label = None
    duration_minutes: int = Field(gt=0, le=720)
    sections: list[SectionIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_versions(self):
        if len({s.paper_version_id for s in self.sections}) != len(self.sections):
            raise ValueError("a paper version may appear only once")
        return self


class ScheduleIn(AudienceIn):
    branch_id: UUID
    name: str | None = Field(default=None, min_length=3, max_length=255)
    start_at: datetime
    end_at: datetime
    generate_at: datetime | None = None
    auto_publish: bool = True
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|DRAFT)$")

    @model_validator(mode="after")
    def window(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.generate_at and self.generate_at > self.start_at:
            raise ValueError("generate_at must not be after start_at")
        return self


class SectionOut(BaseModel):
    position: int
    subject_id: UUID
    subject: str | None
    paper_id: UUID
    paper_title: str
    paper_version_id: UUID
    version_no: int
    version_status: str
    question_count: int
    total_marks: Decimal


class GlobalTestOut(BaseModel):
    test_id: UUID
    name: str
    description: str | None
    exam_type: str | None
    target_class: str | None
    duration_minutes: int
    status: str
    derived_from_test_id: UUID | None
    created_at: datetime
    published_at: datetime | None
    publisher: str | None
    question_count: int
    total_marks: Decimal
    sections: list[SectionOut]


def _is_platform(p: Principal) -> bool:
    return p.is_global and p.has_perm("examplan.manage")


def _require_platform(db: Session, p: Principal) -> None:
    if not _is_platform(p):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "global_test.manage"})
        raise api_error(403, "FORBIDDEN", "Only platform administrators manage Global Tests.")


def _test(db: Session, p: Principal, test_id: UUID) -> GlobalTest:
    t = db.get(GlobalTest, test_id)
    # Providers see published tests only; drafts and withdrawn tests are not revealed to them.
    if not t or (t.status != "PUBLISHED" and not _is_platform(p)):
        raise api_error(404, "NOT_FOUND", "Global test not found.")
    return t


def _out(db: Session, t: GlobalTest) -> GlobalTestOut:
    rows = db.execute(select(GlobalTestSection, PaperVersion, Paper)
                      .join(PaperVersion, PaperVersion.paper_version_id == GlobalTestSection.paper_version_id)
                      .join(Paper, Paper.paper_id == PaperVersion.paper_id)
                      .where(GlobalTestSection.test_id == t.test_id).order_by(GlobalTestSection.position)).all()
    names = {sid: n for sid, n in db.execute(select(Subject.subject_id, Subject.name).where(Subject.subject_id.in_({s.subject_id for s, _, _ in rows})))} if rows else {}
    sections = [SectionOut(position=s.position, subject_id=s.subject_id, subject=names.get(s.subject_id), paper_id=pp.paper_id, paper_title=pp.title,
                           paper_version_id=v.paper_version_id, version_no=v.version_no, version_status=v.status,
                           question_count=v.question_count, total_marks=v.total_marks) for s, v, pp in rows]
    publisher = db.get(Staff, t.published_by) if t.published_by else None
    return GlobalTestOut(test_id=t.test_id, name=t.name, description=t.description, exam_type=t.exam_type, target_class=t.target_class,
                         duration_minutes=t.duration_minutes, status=t.status, derived_from_test_id=t.derived_from_test_id,
                         created_at=t.created_at, published_at=t.published_at, publisher=publisher.full_name if publisher else None,
                         question_count=sum(x.question_count for x in sections), total_marks=sum((x.total_marks for x in sections), Decimal(0)),
                         sections=sections)


def _write_sections(db: Session, t: GlobalTest, sections: list[SectionIn]) -> None:
    """Every section is an approved/published version of a GLOBAL paper, one per subject."""
    for old in db.scalars(select(GlobalTestSection).where(GlobalTestSection.test_id == t.test_id)):
        db.delete(old)
    db.flush()
    subjects = set()
    for pos, s in enumerate(sections, start=1):
        v = db.get(PaperVersion, s.paper_version_id)
        paper = db.get(Paper, v.paper_id) if v else None
        if not paper or paper.scope != "GLOBAL":
            raise api_error(422, "PAPER_NOT_AVAILABLE", "A section must be a version of a platform (global) paper.")
        if v.status not in USABLE:
            raise api_error(422, "VERSION_NOT_APPROVED", f"'{paper.title}' v{v.version_no} is {v.status}; approve it first.")
        if paper.subject_id in subjects:
            raise api_error(422, "DUPLICATE_SUBJECT", "Each subject may appear once in a test.")
        subjects.add(paper.subject_id)
        db.add(GlobalTestSection(test_id=t.test_id, position=pos, subject_id=paper.subject_id, paper_version_id=v.paper_version_id))


@router.get("/global-tests", response_model=Page[GlobalTestOut])
def list_global_tests(status: str | None = Query(default=None, pattern="^(DRAFT|PUBLISHED|WITHDRAWN)$"), q: str | None = Query(default=None, max_length=100),
                      page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.has_perm("examplan.manage") or p.has_perm("exam.read")):
        p.require(db, "exam.read")
    stmt = select(GlobalTest)
    if not _is_platform(p):
        stmt = stmt.where(GlobalTest.status == "PUBLISHED")
    elif status:
        stmt = stmt.where(GlobalTest.status == status)
    if q:
        stmt = stmt.where(func.lower(GlobalTest.name).contains(q.lower()))
    res = paginate(db, stmt.order_by(GlobalTest.created_at.desc()), page, page_size)
    res["items"] = [_out(db, t) for t in res["items"]]
    return res


@router.get("/global-tests/{test_id}", response_model=GlobalTestOut)
def get_global_test(test_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.has_perm("examplan.manage") or p.has_perm("exam.read")):
        p.require(db, "exam.read")
    return _out(db, _test(db, p, test_id))


@router.post("/global-tests", response_model=GlobalTestOut, status_code=201)
def create_global_test(body: GlobalTestIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    t = GlobalTest(created_by=p.staff_id, **body.model_dump(exclude={"sections"}))
    db.add(t)
    db.flush()
    _write_sections(db, t, body.sections)
    db.commit()
    audit.record(db, "global_test.create", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=t.test_id, ip=p.ip)
    return _out(db, t)


@router.put("/global-tests/{test_id}", response_model=GlobalTestOut)
def update_global_test(test_id: UUID, body: GlobalTestIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    t = _test(db, p, test_id)
    if t.status != "DRAFT":
        raise api_error(409, "GLOBAL_TEST_LOCKED", "A published or withdrawn test never changes; derive a new test instead.")
    for k, v in body.model_dump(exclude={"sections"}).items():
        setattr(t, k, v)
    _write_sections(db, t, body.sections)
    db.commit()
    audit.record(db, "global_test.update", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, ip=p.ip)
    return _out(db, t)


@router.post("/global-tests/{test_id}/publish", response_model=GlobalTestOut)
def publish_global_test(test_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    t = _test(db, p, test_id)
    if t.status != "DRAFT":
        raise api_error(409, "INVALID_TRANSITION", f"Only a draft can be published; this test is {t.status}.")
    sections = db.scalars(select(GlobalTestSection).where(GlobalTestSection.test_id == test_id)).all()
    if not sections:
        raise api_error(422, "NO_SECTIONS", "Add at least one section before publishing.")
    for s in sections:   # a paper version may have been withdrawn since it was added
        v = db.get(PaperVersion, s.paper_version_id)
        if v.status not in USABLE:
            raise api_error(422, "VERSION_NOT_APPROVED", f"A section's paper version is {v.status}; replace it before publishing.")
    t.status, t.published_at, t.published_by = "PUBLISHED", utcnow(), p.staff_id
    db.commit()
    audit.record(db, "global_test.publish", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, ip=p.ip)
    return _out(db, t)


class Reason(Strict):
    reason: str


@router.post("/global-tests/{test_id}/withdraw", response_model=GlobalTestOut)
def withdraw_global_test(test_id: UUID, body: Reason, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Stops new schedules. Plans already scheduled stop generating; exams already generated are unchanged."""
    _require_platform(db, p)
    reason = need_reason(body.reason)
    # Same row lock as generation (exam_generation.require_published_global_test): a generation in progress finishes first and keeps
    # its exam; every later generation sees WITHDRAWN.
    t = db.scalar(select(GlobalTest).where(GlobalTest.test_id == test_id).with_for_update())
    if not t:
        raise api_error(404, "NOT_FOUND", "Global test not found.")
    if t.status != "PUBLISHED":
        raise api_error(409, "INVALID_TRANSITION", "Only a published test can be withdrawn.")
    t.status, t.withdrawn_at = "WITHDRAWN", utcnow()
    for plan in db.scalars(select(ExamPlan).where(ExamPlan.global_test_id == test_id, ExamPlan.status.in_(("ACTIVE", "DRAFT")))):
        plan.status = "INACTIVE"
    db.commit()
    audit.record(db, "global_test.withdraw", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, reason=reason, ip=p.ip)
    return _out(db, t)


@router.post("/global-tests/{test_id}/derive", response_model=GlobalTestOut, status_code=201)
def derive_global_test(test_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A new DRAFT copying this test's settings and sections; the original stays exactly as it is."""
    _require_platform(db, p)
    src = _test(db, p, test_id)
    t = GlobalTest(name=f"{src.name} (copy)"[:255], description=src.description, exam_type=src.exam_type, target_class=src.target_class,
                   duration_minutes=src.duration_minutes, derived_from_test_id=src.test_id, created_by=p.staff_id)
    db.add(t)
    db.flush()
    for s in db.scalars(select(GlobalTestSection).where(GlobalTestSection.test_id == test_id).order_by(GlobalTestSection.position)):
        db.add(GlobalTestSection(test_id=t.test_id, position=s.position, subject_id=s.subject_id, paper_version_id=s.paper_version_id))
    db.commit()
    audit.record(db, "global_test.derive", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=t.test_id, ip=p.ip,
                 details={"from": str(test_id)})
    return _out(db, t)


@router.post("/global-tests/{test_id}/schedule", response_model=PlanOut, status_code=201)
def schedule_global_test(test_id: UUID, body: ScheduleIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A provider schedules a published Global Test for its own audience. The resulting plan names the test's exact paper versions
    and uses its duration and per-question marking; those can never be changed on the plan, so the published test is never modified."""
    br = _branch(db, body.branch_id)
    require_plan_scope(db, p, br, body.audience_type)
    t = _test(db, p, test_id)
    if t.status != "PUBLISHED":
        raise api_error(409, "NOT_PUBLISHED", "Only a published Global Test can be scheduled.")
    sections = db.scalars(select(GlobalTestSection).where(GlobalTestSection.test_id == test_id).order_by(GlobalTestSection.position)).all()
    for s in sections:
        if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == br.provider_id, ProviderSubject.subject_id == s.subject_id)):
            subj = db.get(Subject, s.subject_id)
            raise api_error(422, "SUBJECT_NOT_ENABLED", f"Offer {subj.name if subj else 'every subject of the test'} first: the test includes it.")
    missing = validate_audience(db, br, body)
    if missing:
        raise api_error_detail(422, "STUDENTS_NOT_ENROLLED", "Some students are not actively enrolled with this provider.", student_ids=missing[:100])
    duplicate = db.scalar(select(ExamPlan).where(ExamPlan.global_test_id == test_id, ExamPlan.branch_id == br.branch_id,
                                                 ExamPlan.start_at == body.start_at, ExamPlan.status.in_(("ACTIVE", "DRAFT"))))
    if duplicate:
        raise api_error_detail(409, "ALREADY_SCHEDULED", "This test is already scheduled for this branch at that time.", plan_id=str(duplicate.plan_id))
    plan = ExamPlan(provider_id=br.provider_id, branch_id=br.branch_id, name=body.name or t.name, description=t.description,
                    duration_minutes=t.duration_minutes, start_at=body.start_at, end_at=body.end_at, generate_at=body.generate_at,
                    auto_publish=body.auto_publish, status=body.status, exam_type=t.exam_type, target_class=t.target_class,
                    marking_mode="PER_QUESTION", global_test_id=t.test_id, created_by=p.staff_id)
    db.add(plan)
    db.flush()
    write_audience(db, plan, body)
    for s in sections:
        db.add(ExamPlanSubject(plan_id=plan.plan_id, position=s.position, subject_id=s.subject_id, paper_version_id=s.paper_version_id))
    db.commit()
    audit.record(db, "global_test.schedule", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan.plan_id,
                 provider_id=br.provider_id, branch_id=br.branch_id, ip=p.ip, details={"global_test_id": str(test_id), "audience": body.audience_type})
    return _plan_out(db, plan)
