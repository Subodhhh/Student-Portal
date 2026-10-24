"""Global Tests: definitions managed by the platform roles (Super Admin / Admin with examplan.manage). Lifecycle DRAFT -> PUBLISHED ->
WITHDRAWN; a published definition never changes (a different test is a new record, `derive`).

Two kinds:
  * SCHEDULE (every new test): the platform defines the subjects, duration and marking and schedules occurrences centrally
    (routes/global_schedule.py); every provider then submits its OWN papers per occurrence, subject and audience. No platform paper is
    needed. Publishing the definition publishes its draft occurrences.
  * SECTIONS (earlier tests): a list of exact approved GLOBAL paper versions that providers schedule for their own students through an
    exam plan locked to those papers (`schedule`). Kept working for existing tests."""
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
from institute_common.core.class_levels import TargetClass
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import ExamPlan, ExamPlanSubject, GlobalTest, GlobalTestOccurrence, GlobalTestSection, GlobalTestSubject
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
    target_class: TargetClass = None
    duration_minutes: int = Field(gt=0, le=720)
    subject_ids: list[UUID] = Field(default_factory=list, max_length=20)     # SCHEDULE test: providers supply the papers
    sections: list[SectionIn] = Field(default_factory=list, max_length=20)   # SECTIONS test (earlier model)
    marking_mode: str = Field(default="PER_QUESTION", pattern="^(UNIFORM|PER_QUESTION)$")
    marks_correct: int | None = Field(default=None, ge=1, le=100)
    marks_wrong: int | None = Field(default=None, ge=0, le=100)
    generate_lead_minutes: int = Field(default=60, ge=0, le=10080)

    @model_validator(mode="after")
    def rules(self):
        if bool(self.subject_ids) == bool(self.sections):
            raise ValueError("choose the subjects of the test (providers then submit their papers)")
        if len({s.paper_version_id for s in self.sections}) != len(self.sections):
            raise ValueError("a paper version may appear only once")
        if len(set(self.subject_ids)) != len(self.subject_ids):
            raise ValueError("a subject is selected twice")
        if self.marking_mode == "UNIFORM" and (self.marks_correct is None or self.marks_wrong is None):
            raise ValueError("uniform marking needs marks for a correct and for a wrong answer")
        if self.marking_mode == "PER_QUESTION" and (self.marks_correct is not None or self.marks_wrong is not None):
            raise ValueError("per-question marking takes the marks from each paper")
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
    kind: str = "SECTIONS"
    subjects: list[dict] = []
    marking_mode: str = "PER_QUESTION"
    marks_correct: int | None = None
    marks_wrong: int | None = None
    generate_lead_minutes: int = 60
    occurrences: dict = {}                 # counts per status, plus how many were generated
    next_occurrence_at: datetime | None = None


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
    subjects = [{"subject_id": sid, "subject": name} for sid, name in db.execute(
        select(GlobalTestSubject.subject_id, Subject.name).join(Subject, Subject.subject_id == GlobalTestSubject.subject_id)
        .where(GlobalTestSubject.test_id == t.test_id).order_by(GlobalTestSubject.position))]
    counts = {status: n for status, n in db.execute(select(GlobalTestOccurrence.status, func.count()).where(GlobalTestOccurrence.test_id == t.test_id)
                                                      .group_by(GlobalTestOccurrence.status))}
    counts["GENERATED"] = db.scalar(select(func.count()).where(GlobalTestOccurrence.test_id == t.test_id, GlobalTestOccurrence.generated_at.is_not(None))) or 0
    upcoming = db.scalar(select(func.min(GlobalTestOccurrence.start_at)).where(GlobalTestOccurrence.test_id == t.test_id,
                                                                              GlobalTestOccurrence.status == "PUBLISHED", GlobalTestOccurrence.start_at > utcnow()))
    return GlobalTestOut(test_id=t.test_id, name=t.name, description=t.description, exam_type=t.exam_type, target_class=t.target_class,
                         duration_minutes=t.duration_minutes, status=t.status, derived_from_test_id=t.derived_from_test_id,
                         created_at=t.created_at, published_at=t.published_at, publisher=publisher.full_name if publisher else None,
                         question_count=sum(x.question_count for x in sections), total_marks=sum((x.total_marks for x in sections), Decimal(0)),
                         sections=sections, kind=t.kind, subjects=subjects, marking_mode=t.marking_mode, marks_correct=t.marks_correct,
                         marks_wrong=t.marks_wrong, generate_lead_minutes=t.generate_lead_minutes, occurrences=counts, next_occurrence_at=upcoming)


def _write_subjects(db: Session, t: GlobalTest, subject_ids: list[UUID]) -> None:
    for old in db.scalars(select(GlobalTestSubject).where(GlobalTestSubject.test_id == t.test_id)):
        db.delete(old)
    db.flush()
    for pos, sid in enumerate(subject_ids, start=1):
        subj = db.get(Subject, sid)
        if not subj or subj.status != "ACTIVE":
            raise api_error(422, "SUBJECT_NOT_AVAILABLE", "A selected subject does not exist or is inactive.")
        db.add(GlobalTestSubject(test_id=t.test_id, subject_id=sid, position=pos))


def _write_definition(db: Session, t: GlobalTest, body: "GlobalTestIn") -> None:
    for k, v in body.model_dump(exclude={"sections", "subject_ids"}).items():
        setattr(t, k, v)
    t.kind = "SCHEDULE" if body.subject_ids else "SECTIONS"
    db.flush()
    _write_subjects(db, t, body.subject_ids)
    _write_sections(db, t, body.sections)


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
    t = GlobalTest(created_by=p.staff_id, name=body.name, duration_minutes=body.duration_minutes)
    db.add(t)
    _write_definition(db, t, body)
    db.commit()
    audit.record(db, "global_test.create", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=t.test_id, ip=p.ip)
    return _out(db, t)


@router.put("/global-tests/{test_id}", response_model=GlobalTestOut)
def update_global_test(test_id: UUID, body: GlobalTestIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    t = _test(db, p, test_id)
    if t.status != "DRAFT":
        raise api_error(409, "GLOBAL_TEST_LOCKED", "A published or withdrawn test never changes; derive a new test instead.")
    _write_definition(db, t, body)
    db.commit()
    audit.record(db, "global_test.update", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, ip=p.ip)
    return _out(db, t)


class PublishIn(Strict):
    through: datetime | None = None     # SCHEDULE test: publish the draft occurrences starting up to this moment (default: all)


@router.post("/global-tests/{test_id}/publish", response_model=GlobalTestOut)
def publish_global_test(test_id: UUID, body: PublishIn | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    t = _test(db, p, test_id)
    if t.status != "DRAFT":
        raise api_error(409, "INVALID_TRANSITION", f"Only a draft can be published; this test is {t.status}.")
    if t.kind == "SCHEDULE":
        if not db.scalar(select(GlobalTestSubject.gts_id).where(GlobalTestSubject.test_id == test_id).limit(1)):
            raise api_error(422, "NO_SUBJECTS", "Choose the subjects of the test before publishing.")
        published = publish_occurrences(db, t, p.staff_id, body.through if body else None)
        if not published:
            raise api_error(422, "NO_OCCURRENCES", "Schedule at least one future date before publishing.")
        t.status, t.published_at, t.published_by = "PUBLISHED", utcnow(), p.staff_id
        db.commit()
        audit.record(db, "global_test.publish", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, ip=p.ip,
                     details={"occurrences": published})
        return _out(db, t)
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
    for occ in db.scalars(select(GlobalTestOccurrence).where(GlobalTestOccurrence.test_id == test_id, GlobalTestOccurrence.generated_at.is_(None),
                                                            GlobalTestOccurrence.status.in_(("DRAFT", "PUBLISHED")))):
        occ.status, occ.cancelled_at, occ.cancel_reason = "CANCELLED", utcnow(), f"Test withdrawn: {reason}"[:1000]
    db.commit()
    audit.record(db, "global_test.withdraw", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, reason=reason, ip=p.ip)
    return _out(db, t)


@router.post("/global-tests/{test_id}/derive", response_model=GlobalTestOut, status_code=201)
def derive_global_test(test_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A new DRAFT copying this test's settings and sections; the original stays exactly as it is."""
    _require_platform(db, p)
    src = _test(db, p, test_id)
    t = GlobalTest(name=f"{src.name} (copy)"[:255], description=src.description, exam_type=src.exam_type, target_class=src.target_class,
                   duration_minutes=src.duration_minutes, derived_from_test_id=src.test_id, created_by=p.staff_id, kind=src.kind,
                   marking_mode=src.marking_mode, marks_correct=src.marks_correct, marks_wrong=src.marks_wrong,
                   generate_lead_minutes=src.generate_lead_minutes)
    db.add(t)
    db.flush()
    for s in db.scalars(select(GlobalTestSubject).where(GlobalTestSubject.test_id == test_id).order_by(GlobalTestSubject.position)):
        db.add(GlobalTestSubject(test_id=t.test_id, subject_id=s.subject_id, position=s.position))
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
    if t.kind == "SCHEDULE":
        raise api_error(409, "SCHEDULED_CENTRALLY", "This Global Test is scheduled by the platform; submit your papers for its dates instead.")
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


def publish_occurrences(db: Session, t: GlobalTest, actor: UUID, through: datetime | None = None) -> int:
    """Publish the DRAFT occurrences of `t` that start in the future (and up to `through`). Returns how many. No commit."""
    n = 0
    for occ in db.scalars(select(GlobalTestOccurrence).where(GlobalTestOccurrence.test_id == t.test_id, GlobalTestOccurrence.status == "DRAFT")
                          .order_by(GlobalTestOccurrence.start_at)):
        if occ.start_at <= utcnow() or (through and occ.start_at > through):
            continue
        occ.status, occ.published_at, occ.published_by = "PUBLISHED", utcnow(), actor
        n += 1
    return n
