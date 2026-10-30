"""Global Tests, central schedule (SCHEDULE tests): the platform plans the occurrences, providers submit their own papers.

Platform (Super Admin / Admin with examplan.manage):
  plan dates for one or several months at once, add/edit/delete DRAFT occurrences, publish them (with the definition the first time,
  then per planning period), cancel a published occurrence that was not generated yet, see every provider's submissions and coverage.

Provider staff (Provider Admin, Branch Admin, Teacher - permission `paper.upload`, scoped like every other paper action):
  see the published schedule of the tests that concern them (a subject their provider offers; teachers: a subject they teach), and
  submit, per occurrence and subject, one of their papers for an audience they are authorized for (the Create Test audience: whole
  branches, batches, individual students - a tutor without batches picks students). The backend checks the paper (own provider and
  subject; teachers: their own papers) and the audience (routes/tests.authorize with paper.upload), and refuses a submission of the SAME
  paper version for students another active submission of that subject already reaches (a duplicate; different papers for the same
  students are allowed: those students choose one). Submissions close at the deadline (start - generate_lead_minutes), when
  services/global_generation.py makes the date's one exam with every student's eligible papers.

Visibility: platform - everything (metadata only; papers stay in the paper service's own access rules); provider admins - their
provider's submissions; branch admins - their branch's; teachers - their own."""
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.clients.student_client import StudentClient, StudentServiceError, get_student_client
from institute_common.core import audit
from institute_common.core.class_levels import class_target
from institute_common.core.roles import GLOBAL_ROLES, Role
from institute_common.config import settings
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
from inst_exam.services import unified

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
    cache = db.info.setdefault("gs_subjects", {})           # per request: a list asks once per date of the same test
    if test_id not in cache:
        cache[test_id] = list(db.execute(select(GlobalTestSubject.subject_id, Subject.name).join(Subject, Subject.subject_id == GlobalTestSubject.subject_id)
                                         .where(GlobalTestSubject.test_id == test_id).order_by(GlobalTestSubject.position)))
    return cache[test_id]


def _provider_staff(p: Principal) -> bool:
    return not p.is_global and p.has_perm(UPLOAD)


def _my_providers(p: Principal) -> set[UUID]:
    return {a.provider_id for a in p.assignments if a.provider_id and Role(a.role) not in GLOBAL_ROLES}


def _my_subjects(db: Session, p: Principal, provider_id: UUID) -> set[UUID]:
    """Subjects the caller may submit for at this provider: all the provider offers (its admins), the taught ones (teachers)."""
    cache = db.info.setdefault("gs_offered", {})            # per request: asked for every date of a list
    if provider_id not in cache:
        cache[provider_id] = {s for (s,) in db.execute(select(ProviderSubject.subject_id).where(ProviderSubject.provider_id == provider_id))}
    offered = cache[provider_id]
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


def _occ_out(db: Session, p: Principal, occ: GlobalTestOccurrence, test: GlobalTest, *, detail: bool = False, all_subs=None) -> dict:
    if all_subs is None:
        all_subs = db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id == occ.occurrence_id)
                              .order_by(GlobalPaperSubmission.submitted_at)).all()
    subs = [s for s in all_subs if _may_see(p, s)]
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
           "generation_failed_at": occ.generation_failed_at, "generation_attempts": occ.generation_attempts if _is_platform(p) else None,
           "cancel_reason": occ.cancel_reason,
           "subjects": [{"subject_id": sid, "subject": name, "can_submit": sid in mine} for sid, name in subjects],
           "submissions": {"active": sum(1 for s in subs if s.status == "ACTIVE"), "withdrawn": sum(1 for s in subs if s.status == "WITHDRAWN")}}
    if detail:
        out["submission_list"] = _subs_out(db, p, subs)
    return out


def _prefetch_papers(db: Session, subs) -> None:
    """The submissions' paper versions and papers in two statements (then served by the session's identity map to db.get)."""
    have = db.info.setdefault("prefetched_versions", set())
    vids = {s.paper_version_id for s in subs} - have
    have |= vids
    if vids:
        versions = db.scalars(select(PaperVersion).where(PaperVersion.paper_version_id.in_(vids))).all()
        papers = db.scalars(select(Paper).where(Paper.paper_id.in_({v.paper_id for v in versions} or {None}))).all()
        db.info.setdefault("prefetched", []).extend(versions + papers)    # the identity map holds objects weakly: keep them for the request


def _subs_out(db: Session, p: Principal, subs: list[GlobalPaperSubmission]) -> list[dict]:
    if not subs:
        return []
    _prefetch_papers(db, subs)
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
    shown = rows[(page - 1) * page_size: page * page_size]
    subs: dict[UUID, list] = {o.occurrence_id: [] for o, _t in shown}
    if shown:                                  # the page's submissions in one statement, not one per date
        for sub in db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id.in_(list(subs)))
                              .order_by(GlobalPaperSubmission.submitted_at)):
            subs[sub.occurrence_id].append(sub)
    return {"items": [_occ_out(db, p, o, t, all_subs=subs[o.occurrence_id]) for o, t in shown], "total": len(rows), "page": page,
            "page_size": page_size}


@router.get("/global-occurrences/{occurrence_id}")
def get_occurrence(occurrence_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    db.info["audience_snapshot"] = True     # this read-only page resolves every submission (twice): one input snapshot for its providers
    occ, t = _occurrence(db, p, occurrence_id)
    subs = db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id == occ.occurrence_id)
                      .order_by(GlobalPaperSubmission.submitted_at)).all()           # the date's submissions, read once for the whole page
    db.info.setdefault("occurrence_submissions", {})[occ.occurrence_id] = subs
    db.info["audience_snapshot_providers"] = {s.provider_id for s in subs if s.status == "ACTIVE"}
    out = _occ_out(db, p, occ, t, detail=True, all_subs=subs)
    out["coverage"] = coverage(db, p, occ, t)
    out["question_limit"] = question_limit(db, p, subs)
    if occ.generated_at:
        plans = db.scalars(select(ExamPlan).where(ExamPlan.global_occurrence_id == occ.occurrence_id)).all()
        visible = {x.plan_id for x in (daily.visible_plans(db, p, plans) if not _is_platform(p) else plans)}
        out["exams"] = []
        exams = db.scalars(select(Exam).where(Exam.plan_id.in_([x.plan_id for x in plans] or [None]))).all()
        unified.prefetch_unified_exams(db, [e.exam_id for e in exams])                  # one statement, not one per exam
        counts = dict(db.execute(select(StudentExamAssignment.exam_id, func.count()).where(StudentExamAssignment.exam_id.in_([e.exam_id for e in exams] or [None]))
                                 .group_by(StudentExamAssignment.exam_id)).all())
        for e in exams:
            if unified.is_unified_exam(db, e.exam_id):      # by contribution: own subjects' students only, never by anchor
                s = unified.scope(db, p, "exam.read", e)
                if s is not None and not s.ids:
                    continue
                assigned = len(s.ids) if s is not None else counts.get(e.exam_id, 0)
            elif e.plan_id in visible:
                assigned = counts.get(e.exam_id, 0)
            else:
                continue
            out["exams"].append({"exam_id": e.exam_id, "name": e.name, "status": e.status, "assigned": assigned})
    return out


@router.get("/global-occurrences/{occurrence_id}/student-subjects")
def student_subjects(occurrence_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                     sc: StudentClient = Depends(get_student_client)):
    """Per eligible student of a generated date: every configured subject in order, with the paper's institute or the reason there is
    none, and the student's one exam. Platform roles see everything. Provider and branch admins see only students enrolled with them
    (in their branches) and only the subjects attributable to their institute: its own papers, and missing/conflicting subjects it is
    involved in; never another institute's papers. Teachers are refused (they see their students' results on the exam pages)."""
    from institute_common.models.exams import Exam, GlobalTestStudent, GlobalTestStudentSubject
    from institute_common.models.org import Enrollment
    from inst_exam.services.global_layout import message
    occ, t = _occurrence(db, p, occurrence_id)
    platform = _is_platform(p)
    mine: dict[UUID, set[UUID] | None] = {}                       # provider -> branches the caller sees (None = all of them)
    if not platform:
        for a in p.assignments:
            role = Role(a.role)
            if a.provider_id and role in (Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN) and p.can("exam.read", provider_id=a.provider_id, branch_id=a.branch_id):
                if role == Role.PROVIDER_ADMIN:
                    mine[a.provider_id] = None
                elif mine.get(a.provider_id, set()) is not None:
                    mine.setdefault(a.provider_id, set()).add(a.branch_id)
        if not mine:
            audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "exam.read", "occurrence_id": str(occurrence_id)})
            raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
    subjects = _subjects(db, t.test_id)
    rows = db.execute(select(GlobalTestStudent, Exam.status).outerjoin(Exam, Exam.exam_id == GlobalTestStudent.exam_id)
                      .where(GlobalTestStudent.occurrence_id == occurrence_id).order_by(GlobalTestStudent.student_id)).all()
    if not platform:
        enrolled: dict[str, set[UUID]] = {}
        for sid, prov, br in db.execute(select(Enrollment.student_id, Enrollment.provider_id, Enrollment.branch_id)
                                        .where(Enrollment.student_id.in_([r.student_id for r, _ in rows] or [""]), Enrollment.provider_id.in_(list(mine)))):
            if mine[prov] is None or br in mine[prov]:
                enrolled.setdefault(sid, set()).add(prov)
        rows = [(r, st) for r, st in rows if r.student_id in enrolled]
    cells: dict[str, list] = {}
    for c in db.scalars(select(GlobalTestStudentSubject).where(GlobalTestStudentSubject.occurrence_id == occurrence_id,
                                                               GlobalTestStudentSubject.student_id.in_([r.student_id for r, _ in rows] or [""]))
                        .order_by(GlobalTestStudentSubject.position)):
        cells.setdefault(c.student_id, []).append(c)
    names = dict(subjects)
    providers = dict(db.execute(select(Provider.provider_id, Provider.name)).all())
    try:
        people = daily._names(sc, [r.student_id for r, _ in rows][:200])
    except Exception:  # noqa: BLE001  (names are a convenience; IDs are enough)
        people = {}

    def visible(c, sid) -> bool:
        if platform:
            return True
        involved = {UUID(x) for x in (c.detail or {}).get("providers", [])}
        return (c.status == "PAPER" and c.provider_id in enrolled[sid]) or (c.status != "PAPER" and bool(involved & enrolled[sid]))
    out = []
    for r, exam_status in rows:
        st = people.get(r.student_id)
        out.append({"student_id": r.student_id, "name": f"{st.first_name} {st.last_name}".strip() if st else None, "exam_id": r.exam_id,
                    "exam_status": exam_status,
                    "subjects": [{"subject_id": c.subject_id, "subject": names.get(c.subject_id), "position": c.position, "status": c.status,
                                  "message": message(c.status, names.get(c.subject_id)) if c.status != "PAPER" else None,
                                  "provider_name": providers.get(c.provider_id) if c.status == "PAPER" else None}
                                 for c in cells.get(r.student_id, []) if visible(c, r.student_id)]})
    return {"occurrence_id": occurrence_id, "generated": occ.generated_at is not None, "scope": "ALL" if platform else "PROVIDER",
            "subjects": [{"subject_id": sid, "subject": name, "position": i} for i, (sid, name) in enumerate(subjects, start=1)],
            "students": out}


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
    loaded = db.info.get("occurrence_submissions", {}).get(occ.occurrence_id)
    active = ([s for s in loaded if s.status == "ACTIVE"] if loaded is not None else
              db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id == occ.occurrence_id,
                                                             GlobalPaperSubmission.status == "ACTIVE")).all())   # once, not per provider and subject
    _prefetch_papers(db, active)
    offered_by: dict = {}
    for prov, subj in db.execute(select(ProviderSubject.provider_id, ProviderSubject.subject_id).where(ProviderSubject.provider_id.in_(providers or [None]))):
        offered_by.setdefault(prov, set()).add(subj)
    out = []
    for prov in providers:
        offered = offered_by.get(prov, set())
        allowed = offered if _is_platform(p) else _my_subjects(db, p, prov)
        a = gg.assignments(db, occ, prov)
        for sid, sname in subjects:
            if sid not in offered or sid not in allowed:
                continue
            subs = [s for s in active if s.provider_id == prov and s.subject_id == sid and _may_see(p, s)]
            reached = [per[sid] for per in a["papers"].values() if sid in per and any(_may_see(p, x[0]) for x in per[sid])]
            out.append({"provider_id": prov, "provider_name": names.get(prov), "subject_id": sid, "subject": sname,
                        "submissions": len(subs), "ready": sum(1 for s in subs if s.submission_id not in a["reasons"]),
                        "students_covered": len(reached), "choosing": sum(1 for picks in reached if len(picks) > 1),
                        "missing": not subs})
    return out


PROJECTED = ("VALIDATED", "APPROVED", "PUBLISHED")       # versions that may still be usable at the deadline


def question_limit(db: Session, p: Principal, subs: list[GlobalPaperSubmission]) -> dict:
    """A warning before the deadline (never a refusal): the questions the date's one exam would hold with every active submission
    whose version may still be usable, against the configured limit. The platform sees the totals; provider staff see only whether
    the limit is exceeded and their own submissions' questions (never another provider's figures)."""
    counts = {}
    for s in subs:
        if s.status == "ACTIVE":
            v = db.get(PaperVersion, s.paper_version_id)
            if v and v.status in PROJECTED:
                counts[s.submission_id] = v.question_count
    projected, limit = sum(counts.values()), settings.global_test_max_questions
    platform = _is_platform(p)
    return {"limit": limit, "exceeded": projected > limit, "projected": projected if platform else None,
            "mine": None if platform else sum(counts[s.submission_id] for s in subs if s.submission_id in counts and _may_see(p, s))}


def _named_page(sc, student_ids: list[str], page: int, page_size: int) -> dict:
    ids = sorted(set(student_ids))
    page, page_size = max(page, 1), min(max(page_size, 1), 200)
    chunk = ids[(page - 1) * page_size: page * page_size]
    try:
        people = sc.get_many(chunk)
    except StudentServiceError:
        people = {}
    return {"items": [{"student_id": sid, "name": f"{people[sid].first_name} {people[sid].last_name}".strip() if sid in people else None} for sid in chunk],
            "total": len(ids), "page": page, "page_size": page_size}


@router.get("/global-paper-submissions/{submission_id}/students")
def submission_reach(submission_id: UUID, page: int = 1, page_size: int = 50, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                        sc: StudentClient = Depends(get_student_client)):
    """The students a submission reaches now: exactly the list behind its "N student(s) now" (same resolution, same visibility)."""
    sub = db.get(GlobalPaperSubmission, submission_id)
    if not sub or not _may_see(p, sub):
        raise api_error(404, "NOT_FOUND", "Submission not found.")
    ids = [sid for sid, _e, _b in gg.resolve_submission(db, sub)] if sub.status == "ACTIVE" else []
    return {"submission_id": submission_id, **_named_page(sc, ids, page, page_size)}


@router.get("/global-occurrences/{occurrence_id}/coverage/students")
def coverage_students(occurrence_id: UUID, provider_id: UUID, subject_id: UUID, page: int = 1, page_size: int = 50,
                      p: Principal = Depends(get_principal), db: Session = Depends(get_db), sc: StudentClient = Depends(get_student_client)):
    """The students behind one coverage row's "students covered now" (same computation and the same provider/subject scope)."""
    occ, test = _occurrence(db, p, occurrence_id)
    if not _is_platform(p) and provider_id not in _my_providers(p):
        raise api_error(404, "NOT_FOUND", "Coverage not found.")
    offered = {s for (s,) in db.execute(select(ProviderSubject.subject_id).where(ProviderSubject.provider_id == provider_id))}
    allowed = offered if _is_platform(p) else _my_subjects(db, p, provider_id)
    if subject_id not in offered or subject_id not in allowed or subject_id not in {s for s, _ in _subjects(db, test.test_id)}:
        raise api_error(404, "NOT_FOUND", "Coverage not found.")
    a = gg.assignments(db, occ, provider_id)
    ids = [sid for sid, per in a["papers"].items() if subject_id in per and any(_may_see(p, x[0]) for x in per[subject_id])]
    return {"occurrence_id": occurrence_id, "provider_id": provider_id, "subject_id": subject_id, **_named_page(sc, ids, page, page_size)}


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
    # different papers for the same students are allowed (those students choose one); the SAME paper version twice is a duplicate
    for other in db.scalars(select(GlobalPaperSubmission).where(
            GlobalPaperSubmission.occurrence_id == occurrence_id, GlobalPaperSubmission.subject_id == body.subject_id,
            GlobalPaperSubmission.paper_version_id == v.paper_version_id, GlobalPaperSubmission.status == "ACTIVE")):
        overlap = students & {sid for sid, _e, _b in gg.resolve_submission(db, other)}
        if overlap:
            raise api_error_detail(409, "DUPLICATE_PAPER_SUBMISSION", f"This paper version is already submitted for {len(overlap)} of these "
                                   "students for this subject on this date. Choose other students or another paper.",
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


# ------------------------------------------------------------------ platform: recovery of a date whose generation stopped

class RetryIn(Strict):
    reason: str = Field(max_length=1000)


@router.post("/global-occurrences/{occurrence_id}/generation/retry")
def retry_generation(occurrence_id: UUID, body: RetryIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                     p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Super Admin only, after the cause was investigated: let the worker generate a date whose generation stopped (failed passes or the
    question limit). It reuses the date's frozen plan, so no exam, version or assignment is ever created twice; repeating the request
    when the date has not stopped changes nothing."""
    if not p.has_role(Role.SUPER_ADMIN):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "global_test.generation.retry",
                                                                                         "occurrence_id": str(occurrence_id)})
        raise api_error(403, "FORBIDDEN", "Only the Super Admin can retry a Global Test date's generation.")
    if not idempotency_key or not 8 <= len(idempotency_key.strip()) <= 100:
        raise api_error(422, "IDEMPOTENCY_KEY_REQUIRED", "An Idempotency-Key header of 8-100 characters is required.")
    reason = need_reason(body.reason)
    occ = db.scalar(select(GlobalTestOccurrence).where(GlobalTestOccurrence.occurrence_id == occurrence_id).with_for_update())
    if not occ:
        raise api_error(404, "NOT_FOUND", "Global Test date not found.")
    previous = occ.generation_error
    retried = gg.retry_generation(db, occ)
    if retried:
        audit.record(db, "global_test.occurrence.generation_retry", "SUCCESS", actor=p.staff_id, target_type="global_test_occurrence",
                     target_id=occurrence_id, reason=reason, ip=p.ip, details={"previous_error": (previous or "")[:300]})
    return {"occurrence_id": occurrence_id, "retried": retried, "generated_at": occ.generated_at,
            "generation_failed_at": occ.generation_failed_at, "generation_attempts": occ.generation_attempts}
