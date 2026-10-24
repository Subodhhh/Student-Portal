"""Global Tests, central schedule (SCHEDULE tests): the platform plans the occurrences, providers submit their own papers.

Platform (Super Admin / Admin with examplan.manage):
  plan dates for one or several months at once, add/edit/delete DRAFT occurrences, publish them (with the definition the first time,
  then per planning period), cancel a published occurrence that was not generated yet, see every provider's submissions and coverage.

Provider staff (Provider Admin, Branch Admin, Teacher - permission `paper.upload`, scoped like every other paper action):
  see the published schedule of the tests that concern them (a subject their provider offers; teachers: a subject they teach), and
  submit, per occurrence and subject, one of their papers for an audience they are authorized for (the Create Test audience: whole
  branches, batches, individual students - a tutor without batches picks students). The backend checks the paper (own provider and
  subject; teachers: their own papers), the audience (routes/tests.authorize with paper.upload) and refuses an audience that overlaps
  another active submission of the same provider for that subject. Submissions close at the deadline (start - generate_lead_minutes),
  when services/global_generation.py gives every student their own submission's paper.

Visibility: platform - everything (metadata only; papers stay in the paper service's own access rules); provider admins - their
provider's submissions; branch admins - their branch's; teachers - their own."""
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.clients.student_client import StudentClient, get_student_client
from institute_common.core import audit
from institute_common.core.class_levels import class_target
from institute_common.core.roles import GLOBAL_ROLES, Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import (
    Exam, ExamPlan, GlobalPaperSubmission, GlobalTest, GlobalTestOccurrence, GlobalTestSubject, StudentExamAssignment,
)
from institute_common.models.org import Batch, Branch, Provider
from institute_common.models.papers import Paper, PaperVersion
from institute_common.models.staff import Staff
from institute_common.util import Strict, need_reason
from inst_exam.routes import tests as daily
from inst_exam.routes.exams import api_error_detail
from inst_exam.routes.global_tests import _is_platform, _require_platform, publish_occurrences
from inst_exam.routes.test_series import occurrences as month_occurrences
from inst_exam.services import global_generation as gg

router = APIRouter(prefix="/api/v1", tags=["Global Test schedule"])
UPLOAD = "paper.upload"
MAX_WINDOW = timedelta(days=7)


# ------------------------------------------------------------------ schemas

class OccurrenceIn(Strict):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def window(self):
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("times must include their time zone")
        if self.end_at <= self.start_at:
            raise ValueError("the window must close after it opens")
        if self.end_at - self.start_at > MAX_WINDOW:
            raise ValueError("a test may be open for at most 7 days")
        return self


class PlanIn(Strict):
    months: list[str] = Field(min_length=1, max_length=12)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(min_length=1, max_length=64)
    window_minutes: int = Field(gt=0, le=10080)

    @field_validator("months")
    @classmethod
    def months_ok(cls, v: list[str]) -> list[str]:
        import re
        if any(not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", m) for m in v) or len(set(v)) != len(v):
            raise ValueError("months must be distinct YYYY-MM values")
        return sorted(v)

    @model_validator(mode="after")
    def rules(self):
        from zoneinfo import available_timezones
        if self.timezone not in available_timezones():
            raise ValueError("unknown time zone (use an IANA name such as Asia/Kolkata)")
        if len(set(self.weekdays)) != len(self.weekdays) or not all(0 <= d <= 6 for d in self.weekdays):
            raise ValueError("weekdays must be distinct numbers from 0 (Monday) to 6 (Sunday)")
        return self


class PublishIn(Strict):
    through: datetime | None = None


class ReasonIn(Strict):
    reason: str


class SubmissionIn(Strict):
    subject_id: UUID
    paper_version_id: UUID
    audience: daily.TestAudienceIn


# ------------------------------------------------------------------ helpers

def _schedule_test(db: Session, p: Principal, test_id: UUID) -> GlobalTest:
    t = db.get(GlobalTest, test_id)
    if not t or t.kind != "SCHEDULE" or (t.status != "PUBLISHED" and not _is_platform(p)):
        raise api_error(404, "NOT_FOUND", "Global test not found.")
    return t


def _occurrence(db: Session, p: Principal, occurrence_id: UUID, *, lock: bool = False) -> tuple[GlobalTestOccurrence, GlobalTest]:
    stmt = select(GlobalTestOccurrence).where(GlobalTestOccurrence.occurrence_id == occurrence_id)
    occ = db.scalar(stmt.with_for_update() if lock else stmt)
    test = db.get(GlobalTest, occ.test_id) if occ else None
    if not occ or not test or (not _is_platform(p) and (occ.status == "DRAFT" or test.status == "DRAFT" or not _applies(db, p, test))):
        raise api_error(404, "NOT_FOUND", "Scheduled Global Test not found.")
    return occ, test


def _subjects(db: Session, test_id: UUID) -> list[tuple[UUID, str]]:
    return list(db.execute(select(GlobalTestSubject.subject_id, Subject.name).join(Subject, Subject.subject_id == GlobalTestSubject.subject_id)
                           .where(GlobalTestSubject.test_id == test_id).order_by(GlobalTestSubject.position)))


def _provider_staff(p: Principal) -> bool:
    return not p.is_global and p.has_perm(UPLOAD)


def _my_providers(p: Principal) -> set[UUID]:
    return {a.provider_id for a in p.assignments if a.provider_id and Role(a.role) not in GLOBAL_ROLES}


def _my_subjects(db: Session, p: Principal, provider_id: UUID) -> set[UUID]:
    """Subjects the caller may submit for at this provider: all the provider offers (its admins), the taught ones (teachers)."""
    offered = {s for (s,) in db.execute(select(ProviderSubject.subject_id).where(ProviderSubject.provider_id == provider_id))}
    if p.can(UPLOAD, provider_id=provider_id, exclude_roles=(Role.TEACHER, Role.BRANCH_ADMIN)) or any(
            Role(a.role) == Role.BRANCH_ADMIN and a.provider_id == provider_id for a in p.assignments):
        return offered
    return offered & {t.subject_id for t in p.teaching if t.provider_id == provider_id}


def _applies(db: Session, p: Principal, test: GlobalTest) -> bool:
    """Is this published test relevant to the caller: one of its subjects is offered by their provider (teachers: taught by them)?"""
    if _is_platform(p):
        return True
    subjects = {s for s, _ in _subjects(db, test.test_id)}
    return any(_my_subjects(db, p, prov) & subjects for prov in _my_providers(p))


def _may_see(p: Principal, sub: GlobalPaperSubmission) -> bool:
    if _is_platform(p) or sub.submitted_by == p.staff_id:
        return True
    if p.can(UPLOAD, provider_id=sub.provider_id, exclude_roles=(Role.TEACHER, Role.BRANCH_ADMIN)):
        return True                                       # provider admin: the provider's submissions
    return p.can(UPLOAD, provider_id=sub.provider_id, branch_id=sub.branch_id, exclude_roles=(Role.TEACHER,))   # branch admin


def _may_manage(p: Principal, sub: GlobalPaperSubmission) -> bool:
    """Withdraw: the submitter, or an admin of the submission's provider/branch (never a teacher for someone else's paper)."""
    return sub.submitted_by == p.staff_id or (not p.is_global and _may_see(p, sub) and not _only_teacher(p, sub.provider_id))


def _only_teacher(p: Principal, provider_id: UUID) -> bool:
    return not p.can(UPLOAD, provider_id=provider_id, exclude_roles=(Role.TEACHER,)) and not any(
        Role(a.role) == Role.BRANCH_ADMIN and a.provider_id == provider_id for a in p.assignments)


def _open(test: GlobalTest, occ: GlobalTestOccurrence) -> bool:
    return test.status == "PUBLISHED" and occ.status == "PUBLISHED" and occ.generated_at is None and utcnow() < gg.deadline(test, occ)


def _occ_out(db: Session, p: Principal, occ: GlobalTestOccurrence, test: GlobalTest, *, detail: bool = False) -> dict:
    subs = [s for s in db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id == occ.occurrence_id)
                                  .order_by(GlobalPaperSubmission.submitted_at)) if _may_see(p, s)]
    subjects = _subjects(db, test.test_id)
    mine = set()
    if _provider_staff(p):
        for prov in _my_providers(p):
            mine |= _my_subjects(db, p, prov)
    out = {"occurrence_id": occ.occurrence_id, "test_id": test.test_id, "test_name": test.name, "test_status": test.status,
           "exam_type": test.exam_type, "target_class": test.target_class, "duration_minutes": test.duration_minutes,
           "marking": {"mode": test.marking_mode, "correct": test.marks_correct, "wrong": test.marks_wrong},
           "start_at": occ.start_at, "end_at": occ.end_at, "status": occ.status, "submission_deadline": gg.deadline(test, occ),
           "submissions_open": _open(test, occ), "generated_at": occ.generated_at, "generation_error": occ.generation_error if _is_platform(p) else None,
           "cancel_reason": occ.cancel_reason,
           "subjects": [{"subject_id": sid, "subject": name, "can_submit": sid in mine} for sid, name in subjects],
           "submissions": {"active": sum(1 for s in subs if s.status == "ACTIVE"), "withdrawn": sum(1 for s in subs if s.status == "WITHDRAWN")}}
    if detail:
        out["submission_list"] = _subs_out(db, p, subs)
    return out


def _subs_out(db: Session, p: Principal, subs: list[GlobalPaperSubmission]) -> list[dict]:
    if not subs:
        return []
    names = dict(db.execute(select(Subject.subject_id, Subject.name).where(Subject.subject_id.in_({s.subject_id for s in subs}))).all())
    providers = dict(db.execute(select(Provider.provider_id, Provider.name).where(Provider.provider_id.in_({s.provider_id for s in subs}))).all())
    branch_ids = {s.branch_id for s in subs} | {UUID(b) for s in subs for b in s.audience.get("whole_branch_ids", [])}
    branches = dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_(branch_ids))).all())
    batch_ids = {UUID(b) for s in subs for b in s.audience.get("batch_ids", [])}
    batch_names = dict(db.execute(select(Batch.batch_id, Batch.name).where(Batch.batch_id.in_(batch_ids or {None}))).all())
    people = dict(db.execute(select(Staff.staff_id, Staff.full_name).where(Staff.staff_id.in_({s.submitted_by for s in subs}))).all())
    out = []
    for s in subs:
        v = db.get(PaperVersion, s.paper_version_id)
        paper = db.get(Paper, v.paper_id) if v else None
        a = s.audience
        whole = [branches.get(UUID(b)) for b in a.get("whole_branch_ids", [])]
        out.append({"submission_id": s.submission_id, "subject_id": s.subject_id, "subject": names.get(s.subject_id), "provider_id": s.provider_id,
                    "provider_name": providers.get(s.provider_id), "branch_name": branches.get(s.branch_id), "status": s.status,
                    "paper_title": paper.title if paper else None, "paper_version_id": s.paper_version_id,
                    "version_no": v.version_no if v else None, "version_status": v.status if v else None,
                    "ready": bool(v and v.status in gg.READY), "question_count": v.question_count if v else None,
                    "audience": {"entire_branches": whole, "batches": sorted(batch_names[UUID(b)] for b in a.get("batch_ids", []) if UUID(b) in batch_names),
                                 "students": len(a.get("student_ids", []))},
                    "students_now": len(gg.resolve_submission(db, s)) if s.status == "ACTIVE" else 0,
                    "submitted_by_name": people.get(s.submitted_by), "submitted_at": s.submitted_at, "withdraw_reason": s.withdraw_reason,
                    "can_withdraw": s.status == "ACTIVE" and _may_manage(p, s)})
    return out


# ------------------------------------------------------------------ platform: the schedule

@router.get("/global-tests/{test_id}/occurrences")
def list_occurrences(test_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.has_perm("examplan.manage") or p.has_perm("exam.read") or p.has_perm(UPLOAD)):
        p.require(db, "exam.read")
    t = _schedule_test(db, p, test_id)
    stmt = select(GlobalTestOccurrence).where(GlobalTestOccurrence.test_id == test_id)
    if not _is_platform(p):
        if not _applies(db, p, t):
            raise api_error(404, "NOT_FOUND", "Global test not found.")
        stmt = stmt.where(GlobalTestOccurrence.status != "DRAFT")
    return {"items": [_occ_out(db, p, o, t) for o in db.scalars(stmt.order_by(GlobalTestOccurrence.start_at))]}


def _check_future(occ: OccurrenceIn) -> None:
    if occ.start_at <= utcnow():
        raise api_error(422, "IN_THE_PAST", "A test can only be scheduled in the future.")


@router.post("/global-tests/{test_id}/occurrences", status_code=201)
def add_occurrence(test_id: UUID, body: OccurrenceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    t = _schedule_test(db, p, test_id)
    if t.status == "WITHDRAWN":
        raise api_error(409, "INVALID_TRANSITION", "This Global Test is withdrawn.")
    _check_future(body)
    occ = GlobalTestOccurrence(test_id=test_id, start_at=body.start_at, end_at=body.end_at, created_by=p.staff_id)
    db.add(occ)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "ALREADY_SCHEDULED", "This test is already scheduled at that time.")
    audit.record(db, "global_test.occurrence.create", "SUCCESS", actor=p.staff_id, target_type="global_test_occurrence",
                 target_id=occ.occurrence_id, ip=p.ip, details={"test_id": str(test_id), "start_at": body.start_at.isoformat()})
    return _occ_out(db, p, occ, t)


@router.post("/global-tests/{test_id}/occurrences/plan")
def plan_occurrences(test_id: UUID, body: PlanIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Draft occurrences for several months at once: every chosen weekday at the local start time. Dates already planned are kept as
    they are (repeating the request creates nothing new); past dates are skipped."""
    _require_platform(db, p)
    t = _schedule_test(db, p, test_id)
    if t.status == "WITHDRAWN":
        raise api_error(409, "INVALID_TRANSITION", "This Global Test is withdrawn.")
    now = utcnow()
    created, existing, past = [], 0, 0
    for month in body.months:
        for _d, start, end in month_occurrences(month, body.weekdays, body.start_time, body.timezone, body.window_minutes):
            if start <= now:
                past += 1
                continue
            if db.scalar(select(GlobalTestOccurrence.occurrence_id).where(GlobalTestOccurrence.test_id == test_id, GlobalTestOccurrence.start_at == start)):
                existing += 1
                continue
            try:
                with db.begin_nested():
                    occ = GlobalTestOccurrence(test_id=test_id, start_at=start, end_at=end, created_by=p.staff_id)
                    db.add(occ)
                created.append(occ)
            except IntegrityError:
                existing += 1
    if len(created) > 400:
        db.rollback()
        raise api_error(422, "TOO_MANY", "At most 400 dates can be planned at once.")
    db.commit()
    audit.record(db, "global_test.occurrence.plan", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, ip=p.ip,
                 details={"months": body.months, "created": len(created), "existing": existing})
    return {"created": len(created), "existing": existing, "skipped_past": past, "items": [_occ_out(db, p, o, t) for o in created]}


@router.put("/global-occurrences/{occurrence_id}")
def edit_occurrence(occurrence_id: UUID, body: OccurrenceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    occ, t = _occurrence(db, p, occurrence_id, lock=True)
    if occ.status != "DRAFT":
        raise api_error(409, "OCCURRENCE_LOCKED", "Only a draft date can be changed. Cancel a published date instead.")
    _check_future(body)
    occ.start_at, occ.end_at = body.start_at, body.end_at
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise api_error(409, "ALREADY_SCHEDULED", "This test is already scheduled at that time.")
    audit.record(db, "global_test.occurrence.update", "SUCCESS", actor=p.staff_id, target_type="global_test_occurrence", target_id=occurrence_id, ip=p.ip)
    return _occ_out(db, p, occ, t)


@router.delete("/global-occurrences/{occurrence_id}", status_code=204)
def delete_occurrence(occurrence_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_platform(db, p)
    occ, _t = _occurrence(db, p, occurrence_id, lock=True)
    if occ.status != "DRAFT":
        raise api_error(409, "OCCURRENCE_LOCKED", "Only a draft date can be deleted. Cancel a published date instead.")
    db.delete(occ)
    db.commit()
    audit.record(db, "global_test.occurrence.delete", "SUCCESS", actor=p.staff_id, target_type="global_test_occurrence", target_id=occurrence_id, ip=p.ip)


@router.post("/global-tests/{test_id}/occurrences/publish")
def publish_period(test_id: UUID, body: PublishIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Publish the draft dates of an already published test (up to `through`). A draft test is published with POST
    /global-tests/{id}/publish, which publishes its dates too."""
    _require_platform(db, p)
    t = db.scalar(select(GlobalTest).where(GlobalTest.test_id == test_id).with_for_update())
    if not t or t.kind != "SCHEDULE":
        raise api_error(404, "NOT_FOUND", "Global test not found.")
    if t.status != "PUBLISHED":
        raise api_error(409, "NOT_PUBLISHED", "Publish the Global Test itself first; that publishes its dates.")
    n = publish_occurrences(db, t, p.staff_id, body.through)
    if not n:
        raise api_error(422, "NO_OCCURRENCES", "There is no future draft date to publish.")
    db.commit()
    audit.record(db, "global_test.occurrence.publish", "SUCCESS", actor=p.staff_id, target_type="global_test", target_id=test_id, ip=p.ip,
                 details={"published": n, "through": body.through.isoformat() if body.through else None})
    return {"published": n}


@router.post("/global-occurrences/{occurrence_id}/cancel")
def cancel_occurrence(occurrence_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """A published date whose exams were not generated yet. Submissions stay as history; generated exams are cancelled with the
    existing exam cancellation instead."""
    _require_platform(db, p)
    reason = need_reason(body.reason)
    occ, _t = _occurrence(db, p, occurrence_id, lock=True)
    if occ.status != "PUBLISHED":
        raise api_error(409, "INVALID_TRANSITION", f"This date is {occ.status.lower()}.")
    if occ.generated_at:
        raise api_error(409, "ALREADY_GENERATED", "The exams for this date were already generated; cancel them on the exam pages.")
    occ.status, occ.cancelled_at, occ.cancel_reason = "CANCELLED", utcnow(), reason
    db.commit()
    audit.record(db, "global_test.occurrence.cancel", "SUCCESS", actor=p.staff_id, target_type="global_test_occurrence",
                 target_id=occurrence_id, reason=reason, ip=p.ip)
    return {"status": "CANCELLED"}


# ------------------------------------------------------------------ everyone in scope: the published schedule

@router.get("/global-schedule")
def global_schedule(when: str = Query(default="upcoming", pattern="^(upcoming|past|all)$"), page: int = 1, page_size: int = 50,
                    p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Published dates of the Global Tests that concern the caller, with their own submission state."""
    if not (p.has_perm("examplan.manage") or p.has_perm("exam.read") or p.has_perm(UPLOAD)):
        p.require(db, "exam.read")
    stmt = (select(GlobalTestOccurrence, GlobalTest).join(GlobalTest, GlobalTest.test_id == GlobalTestOccurrence.test_id)
            .where(GlobalTest.kind == "SCHEDULE", GlobalTestOccurrence.status != "DRAFT", GlobalTest.status != "DRAFT"))
    now = utcnow()
    if when == "upcoming":
        stmt = stmt.where(GlobalTestOccurrence.end_at > now).order_by(GlobalTestOccurrence.start_at)
    elif when == "past":
        stmt = stmt.where(GlobalTestOccurrence.end_at <= now).order_by(GlobalTestOccurrence.start_at.desc())
    else:
        stmt = stmt.order_by(GlobalTestOccurrence.start_at)
    applies: dict[UUID, bool] = {}
    rows = []
    for occ, test in db.execute(stmt):
        if test.test_id not in applies:
            applies[test.test_id] = _applies(db, p, test)
        if applies[test.test_id]:
            rows.append((occ, test))
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    return {"items": [_occ_out(db, p, o, t) for o, t in rows[(page - 1) * page_size: page * page_size]], "total": len(rows), "page": page,
            "page_size": page_size}


@router.get("/global-occurrences/{occurrence_id}")
def get_occurrence(occurrence_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    occ, t = _occurrence(db, p, occurrence_id)
    out = _occ_out(db, p, occ, t, detail=True)
    out["coverage"] = coverage(db, p, occ, t)
    if occ.generated_at:
        plans = db.scalars(select(ExamPlan).where(ExamPlan.global_occurrence_id == occ.occurrence_id)).all()
        visible = daily.visible_plans(db, p, plans) if not _is_platform(p) else plans
        exams = db.scalars(select(Exam).where(Exam.plan_id.in_([x.plan_id for x in visible] or [None]))).all()
        out["exams"] = [{"exam_id": e.exam_id, "name": e.name, "status": e.status,
                         "assigned": db.query(StudentExamAssignment).filter_by(exam_id=e.exam_id).count()} for e in exams]
    return out


def coverage(db: Session, p: Principal, occ: GlobalTestOccurrence, test: GlobalTest) -> list[dict]:
    """Per provider (in the caller's scope) and subject of the test: usable submissions, students covered now, and what is missing.
    Teachers see only their own subjects' rows computed from their own submissions."""
    subjects = _subjects(db, test.test_id)
    if _is_platform(p):
        providers = sorted({x for (x,) in db.execute(select(ProviderSubject.provider_id).where(
            ProviderSubject.subject_id.in_([s for s, _ in subjects] or [None])))}, key=str)
    else:
        providers = sorted(_my_providers(p), key=str)
    names = dict(db.execute(select(Provider.provider_id, Provider.name).where(Provider.provider_id.in_(providers or [None]))).all())
    out = []
    for prov in providers:
        offered = {s for (s,) in db.execute(select(ProviderSubject.subject_id).where(ProviderSubject.provider_id == prov))}
        allowed = offered if _is_platform(p) else _my_subjects(db, p, prov)
        a = gg.assignments(db, occ, prov)
        for sid, sname in subjects:
            if sid not in offered or sid not in allowed:
                continue
            subs = [s for s in db.scalars(select(GlobalPaperSubmission).where(
                GlobalPaperSubmission.occurrence_id == occ.occurrence_id, GlobalPaperSubmission.provider_id == prov,
                GlobalPaperSubmission.subject_id == sid, GlobalPaperSubmission.status == "ACTIVE")) if _may_see(p, s)]
            covered = sum(1 for per in a["papers"].values() if sid in per and _may_see(p, per[sid][0]))
            out.append({"provider_id": prov, "provider_name": names.get(prov), "subject_id": sid, "subject": sname,
                        "submissions": len(subs), "ready": sum(1 for s in subs if db.get(PaperVersion, s.paper_version_id).status in gg.READY),
                        "students_covered": covered, "ambiguous": sum(1 for (_st, sj) in a["ambiguous"] if sj == sid),
                        "missing": not subs})
    return out


# ------------------------------------------------------------------ provider staff: submit papers

def _require_submitter(db: Session, p: Principal, provider_id: UUID) -> None:
    """Provider staff only (platform roles schedule; providers decide their papers)."""
    if p.is_global or not p.has_perm(UPLOAD):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "global_test.submit"})
        raise api_error(403, "FORBIDDEN", "Only provider staff submit papers for Global Tests.")
    daily._provider(db, p, provider_id, UPLOAD)


@router.get("/global-occurrences/{occurrence_id}/scope")
def submission_scope(occurrence_id: UUID, provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Branches, batches and subjects of THIS test the caller may submit a paper for at this provider."""
    occ, t = _occurrence(db, p, occurrence_id)
    _require_submitter(db, p, provider_id)
    test_subjects = {s for s, _ in _subjects(db, t.test_id)}
    branches = []
    for b in daily.branch_scope(db, p, provider_id, UPLOAD):
        b["subjects"] = [s for s in b["subjects"] if s["subject_id"] in test_subjects]
        if b["subjects"]:
            branches.append(b)
    prov = db.get(Provider, provider_id)
    return {"provider_id": provider_id, "provider_name": prov.name, "branches": branches, "submissions_open": _open(t, occ)}


@router.get("/global-occurrences/{occurrence_id}/students")
def submission_students(occurrence_id: UUID, provider_id: UUID, branch_ids: list[UUID] = Query(...), q: str | None = Query(default=None, max_length=100),
                        p: Principal = Depends(get_principal), db: Session = Depends(get_db), sc: StudentClient = Depends(get_student_client)):
    """Students the caller may list individually (same rule as Create Test, under paper.upload), with their names."""
    _occurrence(db, p, occurrence_id)
    _require_submitter(db, p, provider_id)
    allowed = {b["branch_id"] for b in daily.branch_scope(db, p, provider_id, UPLOAD)}
    cands = daily.student_candidates(db, p, provider_id, [b for b in branch_ids if b in allowed], UPLOAD)
    names = daily._names(sc, list(cands))
    term = (q or "").strip().lower()
    items = []
    for sid, c in cands.items():
        st = names.get(sid)
        name = f"{st.first_name} {st.last_name}".strip() if st else None
        if term and term not in sid.lower() and term not in (name or "").lower():
            continue
        items.append({"student_id": sid, "name": name, "branches": sorted(c["branches"]), "batches": sorted(c["batches"])})
    return {"items": items[:200], "total": len(items)}


@router.get("/global-occurrences/{occurrence_id}/papers")
def submission_papers(occurrence_id: UUID, provider_id: UUID, subject_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The caller's papers they may submit for one subject (teachers: the papers they uploaded), with their latest usable version."""
    _occurrence(db, p, occurrence_id)
    _require_submitter(db, p, provider_id)
    items = []
    for paper in db.scalars(select(Paper).where(Paper.scope == "PROVIDER", Paper.provider_id == provider_id, Paper.subject_id == subject_id)
                            .order_by(Paper.title)):
        if _paper_problem(db, p, provider_id, subject_id, paper):
            continue
        v = db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper.paper_id, PaperVersion.status.in_(("VALIDATED", "APPROVED", "PUBLISHED")))
                      .order_by(PaperVersion.version_no.desc()).limit(1))
        if v:
            items.append({"paper_id": paper.paper_id, "title": paper.title, "paper_version_id": v.paper_version_id, "version_no": v.version_no,
                          "version_status": v.status, "ready": v.status in gg.READY, "question_count": v.question_count})
    return {"items": items, "total": len(items)}


def _paper_problem(db: Session, p: Principal, provider_id: UUID, subject_id: UUID, paper: Paper | None) -> str | None:
    if not paper or paper.scope != "PROVIDER" or paper.provider_id != provider_id or paper.subject_id != subject_id:
        return "The paper does not belong to your provider and this subject."
    if not p.can(UPLOAD, provider_id=provider_id, branch_id=paper.branch_id, subject_id=subject_id):
        return "The paper is not available to you."
    if _only_teacher(p, provider_id) and paper.created_by != p.staff_id:
        return "Teachers submit only the papers they uploaded."
    return None


@router.post("/global-occurrences/{occurrence_id}/submissions", status_code=201)
def submit_paper(occurrence_id: UUID, body: SubmissionIn, idempotency_key: str = Header(min_length=8, max_length=100),
                 p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    a = body.audience
    _require_submitter(db, p, a.provider_id)
    existing = db.scalar(select(GlobalPaperSubmission).where(GlobalPaperSubmission.client_request_id == idempotency_key))
    if existing:
        if existing.submitted_by != p.staff_id:
            raise api_error(409, "IDEMPOTENCY_KEY_REUSED", "This request key belongs to another submission.")
        return _subs_out(db, p, [existing])[0]
    occ, t = _occurrence(db, p, occurrence_id, lock=True)
    if not _open(t, occ):
        raise api_error(409, "SUBMISSIONS_CLOSED", "Papers for this date are no longer accepted (the deadline has passed, or the date was cancelled).")
    if body.subject_id not in {s for s, _ in _subjects(db, t.test_id)}:
        raise api_error(422, "SUBJECT_NOT_IN_TEST", "This subject is not part of the Global Test.")
    v = db.get(PaperVersion, body.paper_version_id)
    paper = db.get(Paper, v.paper_id) if v else None
    problem = _paper_problem(db, p, a.provider_id, body.subject_id, paper)
    if problem:
        raise api_error(404, "PAPER_NOT_AVAILABLE", problem)
    if v.status not in ("VALIDATED", "APPROVED", "PUBLISHED"):
        raise api_error(409, "VERSION_NOT_USABLE", f"This paper version is {v.status}.")
    problems = daily.authorize(db, p, a, [body.subject_id], UPLOAD)
    if problems:
        raise api_error_detail(422, "AUDIENCE_INVALID", "Some selections are not allowed.", errors=problems)
    # exactly the rule generation applies (global_generation.resolve_submission): this paper's subject and the test's Class
    code, class_problem = class_target(t.target_class)
    students = {sid for sid, _e, _b in daily.resolve(db, a, [body.subject_id], gg.UNMATCHABLE if class_problem else code)}
    if not students:
        raise api_error(422, "AUDIENCE_EMPTY", "Nobody is eligible: no active student in this selection takes this subject"
                        + (f" in Class {t.target_class}" if t.target_class and not class_problem else "") + ".")
    for other in db.scalars(select(GlobalPaperSubmission).where(
            GlobalPaperSubmission.occurrence_id == occurrence_id, GlobalPaperSubmission.provider_id == a.provider_id,
            GlobalPaperSubmission.subject_id == body.subject_id, GlobalPaperSubmission.status == "ACTIVE")):
        overlap = students & {sid for sid, _e, _b in gg.resolve_submission(db, other)}
        if overlap:
            raise api_error_detail(409, "AUDIENCE_OVERLAP", f"{len(overlap)} of these students already receive another paper for this subject "
                                   "on this date. Choose an audience that does not overlap, or ask for that submission to be withdrawn.",
                                   overlapping_students=len(overlap))
    sub = GlobalPaperSubmission(occurrence_id=occurrence_id, subject_id=body.subject_id, provider_id=a.provider_id, branch_id=a.branch_ids[0],
                                paper_version_id=v.paper_version_id, audience=a.model_dump(mode="json"), client_request_id=idempotency_key,
                                submitted_by=p.staff_id)
    db.add(sub)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        sub = db.scalar(select(GlobalPaperSubmission).where(GlobalPaperSubmission.client_request_id == idempotency_key))
        if not sub or sub.submitted_by != p.staff_id:
            raise api_error(409, "IDEMPOTENCY_KEY_REUSED", "This request key belongs to another submission.")
        return _subs_out(db, p, [sub])[0]
    audit.record(db, "global_test.submission.create", "SUCCESS", actor=p.staff_id, target_type="global_paper_submission",
                 target_id=sub.submission_id, provider_id=sub.provider_id, branch_id=sub.branch_id, ip=p.ip,
                 details={"occurrence_id": str(occurrence_id), "subject_id": str(body.subject_id), "paper_version_id": str(v.paper_version_id),
                          "students": len(students)})
    return _subs_out(db, p, [sub])[0]


@router.post("/global-paper-submissions/{submission_id}/withdraw")
def withdraw_submission(submission_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    sub = db.scalar(select(GlobalPaperSubmission).where(GlobalPaperSubmission.submission_id == submission_id).with_for_update())
    if not sub or not _may_see(p, sub):
        raise api_error(404, "NOT_FOUND", "Submission not found.")
    if not _may_manage(p, sub):
        raise api_error(403, "FORBIDDEN", "Only the submitter or an administrator of this branch/provider can withdraw it.")
    reason = need_reason(body.reason)
    occ = db.get(GlobalTestOccurrence, sub.occurrence_id)
    test = db.get(GlobalTest, occ.test_id)
    if sub.status != "ACTIVE":
        raise api_error(409, "INVALID_TRANSITION", "This submission is already withdrawn.")
    if not _open(test, occ):
        raise api_error(409, "SUBMISSIONS_CLOSED", "The deadline has passed; the exams for this date are fixed.")
    sub.status, sub.withdrawn_by, sub.withdrawn_at, sub.withdraw_reason = "WITHDRAWN", p.staff_id, utcnow(), reason
    db.commit()
    audit.record(db, "global_test.submission.withdraw", "SUCCESS", actor=p.staff_id, target_type="global_paper_submission",
                 target_id=submission_id, provider_id=sub.provider_id, branch_id=sub.branch_id, reason=reason, ip=p.ip)
    return _subs_out(db, p, [sub])[0]


@router.get("/global-occurrences/{occurrence_id}/submissions")
def list_submissions(occurrence_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    occ, _t = _occurrence(db, p, occurrence_id)
    subs = [s for s in db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id == occurrence_id)
                                  .order_by(GlobalPaperSubmission.submitted_at)) if _may_see(p, s)]
    return {"items": _subs_out(db, p, subs), "total": len(subs)}

