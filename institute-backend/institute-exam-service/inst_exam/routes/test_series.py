"""Test -> Weekly Test: plan one month of recurring tests at once (permission `test.create`, same roles and scope as a Daily Test).

A Weekly Test has the Daily Test configuration (audience, subjects with their papers, duration, marking, metadata; validated by the
same functions in routes/tests.py) plus a recurrence: chosen weekdays of one month at a local start time in an IANA time zone, each
open for `window_minutes`. Generating the series creates one ordinary exam plan per future occurrence:

  * deterministic - the occurrences are computed from the stored series, never from the request;
  * idempotent - the unique (series_id, start_at) constraint means a retried or concurrent generation finds the existing plan instead
    of creating a second one; creating the series itself is keyed by an Idempotency-Key;
  * each plan generates its exam at `start - generate_lead_minutes` through the normal generator (worker), so the established rules
    apply unchanged: a subject without a published paper is left out and notified, a paper approved later is used by the next
    occurrence, exams already generated never change, and failures are recorded and notified;
  * plans already created are never modified by a later generation; cancelling the series stops only occurrences whose exam has not
    been generated yet.

Every paper slot names a paper (`paper_id`): each occurrence uses that paper's latest PUBLISHED version at its generation time.
"""
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Depends, Header
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.core.taxonomy import Label
from institute_common.database import get_db, utcnow
from institute_common.models.exams import Exam, ExamPlan, ExamPlanBatch, ExamPlanBranch, ExamPlanStudent, ExamPlanSubject, TestSeries
from institute_common.models.org import Branch
from institute_common.models.papers import Paper, PaperVersion
from institute_common.util import Strict, need_reason
from inst_exam.routes import tests as daily
from inst_exam.routes.exams import api_error_detail
from inst_exam.services.notify import notify, responsible_staff

router = APIRouter(prefix="/api/v1", tags=["Weekly Tests"])
PERM = daily.PERM
MAX_OCCURRENCES = 31


class SeriesSubjectIn(Strict):
    subject_id: UUID
    paper_id: UUID


class SeriesIn(Strict):
    audience: daily.TestAudienceIn
    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    subjects: list[SeriesSubjectIn] = Field(min_length=1, max_length=10)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(min_length=1, max_length=64)
    window_minutes: int = Field(gt=0, le=1440)
    duration_minutes: int = Field(gt=0, le=720)
    generate_lead_minutes: int = Field(default=60, ge=0, le=10080)
    marking_mode: str = Field(default="UNIFORM", pattern="^(UNIFORM|PER_QUESTION)$")
    marks_correct: int | None = Field(default=None, ge=1, le=100)
    marks_wrong: int | None = Field(default=None, ge=0, le=100)
    exam_type: Label = None
    target_class: Label = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("the test name must have at least 3 characters")
        return v

    @field_validator("timezone")
    @classmethod
    def known_zone(cls, v: str) -> str:
        if v not in available_timezones():
            raise ValueError("unknown time zone (use an IANA name such as Asia/Kolkata)")
        return v

    @model_validator(mode="after")
    def rules(self):
        if len(set(self.weekdays)) != len(self.weekdays) or not all(0 <= d <= 6 for d in self.weekdays):
            raise ValueError("weekdays must be distinct numbers from 0 (Monday) to 6 (Sunday)")
        if self.duration_minutes > self.window_minutes:
            raise ValueError("the duration must fit inside the window each test is open")
        if len({s.subject_id for s in self.subjects}) != len(self.subjects):
            raise ValueError("a subject is selected twice")
        if self.marking_mode == "UNIFORM" and (self.marks_correct is None or self.marks_wrong is None):
            raise ValueError("uniform marking needs marks for a correct and for a wrong answer")
        if self.marking_mode == "PER_QUESTION" and (self.marks_correct is not None or self.marks_wrong is not None):
            raise ValueError("per-question marking takes the marks from the paper")
        return self


class ReasonIn(Strict):
    reason: str


# ------------------------------------------------------------------ the recurrence

def occurrences(month: str, weekdays: list[int], start_time: str, zone: str, window_minutes: int) -> list[tuple[date, datetime, datetime]]:
    """Every (local date, UTC start, UTC end) of the month on the chosen weekdays, in date order. The local wall-clock time is kept
    across daylight-saving changes (the UTC time moves instead)."""
    year, mon = (int(x) for x in month.split("-"))
    hh, mm = (int(x) for x in start_time.split(":"))
    tz = ZoneInfo(zone)
    out = []
    d = date(year, mon, 1)
    while d.month == mon:
        if d.weekday() in weekdays:
            start = datetime.combine(d, time(hh, mm), tzinfo=tz).astimezone(timezone.utc)
            out.append((d, start, start + timedelta(minutes=window_minutes)))
        d += timedelta(days=1)
    return out


def _series_occurrences(s: TestSeries) -> list[tuple[date, datetime, datetime]]:
    return occurrences(s.month, list(s.weekdays), s.start_time, s.timezone, s.window_minutes)


def _audience(s: TestSeries) -> daily.TestAudienceIn:
    return daily.TestAudienceIn.model_validate(s.audience)


# ------------------------------------------------------------------ validation shared by preview, create and generate

def _problems(db: Session, p: Principal, audience: daily.TestAudienceIn, subjects: list[SeriesSubjectIn]) -> tuple[int, list[dict], list[dict]]:
    """(eligible students now, blocking problems, warnings) for a series configuration, checked against the CALLER's scope."""
    problems = daily.authorize(db, p, audience, [s.subject_id for s in subjects])
    warnings: list[dict] = []
    eligible = 0 if problems else len(daily.resolve(db, audience))
    if not problems and not eligible:
        problems.append({"field": "audience", "value": None, "message": "Nobody is eligible: the selected batches/branches have no active students."})
    for s in subjects:
        paper = db.get(Paper, s.paper_id)
        problem = daily.paper_problem(db, p, audience.provider_id, s.subject_id, paper)
        if problem:
            problems.append({"field": "subjects", "value": str(s.subject_id), "message": problem})
        elif not _has_published_version(db, paper.paper_id):
            warnings.append({"field": "subjects", "value": str(s.subject_id),
                             "message": f"'{paper.title}' has no published version yet: occurrences generated before one is approved "
                                        "leave this subject out and notify the responsible staff."})
    return eligible, problems, warnings


def _has_published_version(db: Session, paper_id: UUID) -> bool:
    return db.scalar(select(PaperVersion.paper_version_id).where(PaperVersion.paper_id == paper_id, PaperVersion.status == "PUBLISHED").limit(1)) is not None


def _schedule(body: SeriesIn, now: datetime) -> tuple[list[dict], list[dict]]:
    """(future occurrences, problems)."""
    all_occ = occurrences(body.month, body.weekdays, body.start_time, body.timezone, body.window_minutes)
    future = [{"date": d.isoformat(), "start_at": st, "end_at": en} for d, st, en in all_occ if st > now]
    problems = []
    if not future:
        problems.append({"field": "month", "value": body.month, "message": "No occurrence of the chosen weekdays is still in the future in this month."})
    if len(future) > MAX_OCCURRENCES:
        problems.append({"field": "weekdays", "value": None, "message": f"At most {MAX_OCCURRENCES} tests per month."})
    return future, problems


def _conflicts(db: Session, provider_id: UUID, branch_ids: list[UUID], windows: list[tuple[datetime, datetime]], series_id: UUID | None = None) -> list[dict]:
    """Other ACTIVE tests of the provider in the same branches whose window overlaps an occurrence (reported, not blocking: an
    institute may run two tests in one day on purpose)."""
    if not windows:
        return []
    lo, hi = min(w[0] for w in windows), max(w[1] for w in windows)
    stmt = select(ExamPlan).where(ExamPlan.provider_id == provider_id, ExamPlan.status == "ACTIVE", ExamPlan.branch_id.in_(branch_ids),
                                  ExamPlan.start_at < hi, ExamPlan.end_at > lo)
    if series_id:
        stmt = stmt.where((ExamPlan.series_id.is_(None)) | (ExamPlan.series_id != series_id))
    out = []
    for plan in db.scalars(stmt.order_by(ExamPlan.start_at).limit(200)):
        if any(plan.start_at < en and plan.end_at > st for st, en in windows):
            out.append({"plan_id": str(plan.plan_id), "name": plan.name, "start_at": plan.start_at.isoformat(), "end_at": plan.end_at.isoformat()})
    return out[:20]


def _require_creator(db: Session, p: Principal) -> None:
    if not p.has_perm(PERM):
        p.require(db, PERM)
    daily.require_test_type(db, p, "WEEKLY")


# ------------------------------------------------------------------ generation

class SeriesGenerationError(Exception):
    pass


def generate_occurrences(db: Session, s: TestSeries, actor: UUID, now: datetime | None = None) -> dict:
    """Create the plan of every future occurrence that does not exist yet. Safe to repeat and to run concurrently. Never modifies an
    existing plan. Each occurrence is its own transaction, so a failure never leaves a half-created plan and keeps the occurrences
    already scheduled; the failure is recorded on the series and notified, and the next attempt continues where this one stopped."""
    now = now or utcnow()
    if s.status != "ACTIVE":
        raise SeriesGenerationError("This Weekly Test is cancelled.")
    audience = _audience(s)
    created, existing, past = [], [], []
    s.generation_attempts = (s.generation_attempts or 0) + 1
    db.commit()
    try:
        for d, start, end in _series_occurrences(s):
            if start <= now:
                past.append(d.isoformat())
                continue
            plan = db.scalar(select(ExamPlan).where(ExamPlan.series_id == s.series_id, ExamPlan.start_at == start))
            if plan:
                existing.append(plan.plan_id)
                continue
            try:
                plan = _new_plan(db, s, audience, d, start, end)
                db.commit()                          # one transaction per occurrence: a later failure keeps the earlier ones
                created.append(plan.plan_id)
            except IntegrityError:                   # a concurrent generation created it first
                db.rollback()
                plan = db.scalar(select(ExamPlan).where(ExamPlan.series_id == s.series_id, ExamPlan.start_at == start))
                if not plan:
                    raise
                existing.append(plan.plan_id)
        s.last_generated_at, s.last_generation_error = utcnow(), None
        db.commit()
    except Exception as exc:  # recorded, notified and re-raised as a clear failure; the plans created so far stay (all valid)
        db.rollback()
        message = f"{type(exc).__name__}: {str(exc)[:300]}"
        s = db.get(TestSeries, s.series_id)
        s.last_generation_error = message
        db.commit()
        audit.record(db, "test_series.generate", "FAILURE", actor=actor, target_type="test_series", target_id=s.series_id,
                     provider_id=s.provider_id, branch_id=s.branch_id, details={"error": message, "created": len(created)})
        staff = set(responsible_staff(db, s.provider_id, s.branch_id, institute=True)) | {s.created_by}
        notify(db, staff, kind="TEST_SERIES_GENERATION_FAILED", title=f"Weekly Test generation failed: {s.name}",
               body=f"Some tests of the Weekly Test '{s.name}' ({s.month}) could not be scheduled: {message}\n\n"
                    "Open Test > Weekly Test and choose 'Generate missing tests' to retry. Tests already scheduled are unchanged.",
               dedupe=f"seriesfail:{s.series_id}:{s.generation_attempts}", provider_id=s.provider_id)
        raise SeriesGenerationError(message) from exc
    if created:
        audit.record(db, "test_series.generate", "SUCCESS", actor=actor, target_type="test_series", target_id=s.series_id,
                     provider_id=s.provider_id, branch_id=s.branch_id, details={"created": len(created), "existing": len(existing)})
    return {"created": [str(x) for x in created], "existing": [str(x) for x in existing], "skipped_past": past}


def _new_plan(db: Session, s: TestSeries, a: daily.TestAudienceIn, d: date, start: datetime, end: datetime) -> ExamPlan:
    plan = ExamPlan(provider_id=s.provider_id, branch_id=s.branch_id, name=s.name, description=s.description,
                    duration_minutes=s.duration_minutes, start_at=start, end_at=end,
                    generate_at=start - timedelta(minutes=s.generate_lead_minutes), auto_publish=True, status="ACTIVE",
                    audience_type="TARGETED", marking_mode=s.marking_mode, marks_correct=s.marks_correct, marks_wrong=s.marks_wrong,
                    exam_type=s.exam_type, target_class=s.target_class, series_id=s.series_id, created_by=s.created_by)
    db.add(plan)
    db.flush()
    for b in a.whole_branch_ids:
        db.add(ExamPlanBranch(plan_id=plan.plan_id, branch_id=b))
    for bid in a.batch_ids:
        db.add(ExamPlanBatch(plan_id=plan.plan_id, batch_id=bid))
    for sid in a.student_ids:
        db.add(ExamPlanStudent(plan_id=plan.plan_id, student_id=sid))
    for pos, subj in enumerate(s.subjects, start=1):
        db.add(ExamPlanSubject(plan_id=plan.plan_id, position=pos, subject_id=UUID(subj["subject_id"]), paper_id=UUID(subj["paper_id"])))
    db.flush()
    return plan


# ------------------------------------------------------------------ output

def _series_plans(db: Session, s: TestSeries) -> list[ExamPlan]:
    return db.scalars(select(ExamPlan).where(ExamPlan.series_id == s.series_id).order_by(ExamPlan.start_at)).all()


def _out(db: Session, s: TestSeries, with_tests: bool = False) -> dict:
    plans = _series_plans(db, s)
    summaries = daily._summaries(db, plans)
    planned = _series_occurrences(s)
    by_start = {sm["start_at"].astimezone(timezone.utc) if sm["start_at"].tzinfo else sm["start_at"].replace(tzinfo=timezone.utc): sm for sm in summaries}
    states: dict[str, int] = {}
    for sm in summaries:
        states[sm["state"]] = states.get(sm["state"], 0) + 1
    now = utcnow()
    missing = [st.isoformat() for _d, st, _e in planned if st > now and st not in by_start] if s.status == "ACTIVE" else []
    names = dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_([UUID(b) for b in s.audience.get("branch_ids", [])] or [None]))).all())
    out = {"series_id": s.series_id, "name": s.name, "description": s.description, "status": s.status, "provider_id": s.provider_id,
           "branch_id": s.branch_id, "month": s.month, "weekdays": s.weekdays, "start_time": s.start_time, "timezone": s.timezone,
           "window_minutes": s.window_minutes, "duration_minutes": s.duration_minutes, "generate_lead_minutes": s.generate_lead_minutes,
           "marking_mode": s.marking_mode, "marks_correct": s.marks_correct, "marks_wrong": s.marks_wrong, "exam_type": s.exam_type,
           "target_class": s.target_class, "subjects": s.subjects, "branches": sorted(names.values()),
           "audience": s.audience, "planned_count": len(planned), "scheduled_count": len(plans), "missing_occurrences": missing,
           "states": states, "last_generated_at": s.last_generated_at, "last_generation_error": s.last_generation_error,
           "created_by": s.created_by, "created_at": s.created_at, "cancelled_at": s.cancelled_at, "cancel_reason": s.cancel_reason}
    if with_tests:
        out["tests"] = summaries
    return out


def _visible(db: Session, p: Principal, s: TestSeries) -> bool:
    plans = _series_plans(db, s)
    if daily._manager(p, s.provider_id, s.branch_id) or s.created_by == p.staff_id:
        return True
    return bool(plans) and bool(daily.visible_plans(db, p, plans[:1]))


def _series(db: Session, p: Principal, series_id: UUID, *, manage: bool = False) -> TestSeries:
    s = db.get(TestSeries, series_id)
    if not s or not _visible(db, p, s):
        raise api_error(404, "NOT_FOUND", "Weekly Test not found.")
    if manage and not daily._manager(p, s.provider_id, s.branch_id):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=s.provider_id, branch_id=s.branch_id,
                     details={"permission": PERM, "series_id": str(series_id)})
        raise api_error(403, "FORBIDDEN", "Only an administrator of this test's branch can change it.")
    return s


# ------------------------------------------------------------------ endpoints

@router.post("/test-series/preview")
def preview_series(body: SeriesIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """The dates that would be scheduled, the audience size, problems and warnings. Writes nothing."""
    _require_creator(db, p)
    daily._provider(db, p, body.audience.provider_id)
    eligible, problems, warnings = _problems(db, p, body.audience, body.subjects)
    future, schedule_problems = _schedule(body, utcnow())
    conflicts = _conflicts(db, body.audience.provider_id, body.audience.branch_ids, [(o["start_at"], o["end_at"]) for o in future])
    return {"ok": not (problems or schedule_problems), "eligible_count": eligible, "occurrences": future, "problems": problems + schedule_problems,
            "warnings": warnings, "conflicts": conflicts}


@router.post("/test-series", status_code=201)
def create_series(body: SeriesIn, idempotency_key: str = Header(min_length=8, max_length=100), p: Principal = Depends(get_principal),
                  db: Session = Depends(get_db)):
    """Create the Weekly Test and schedule all its future occurrences. The same Idempotency-Key returns the same series (and
    schedules whatever is still missing)."""
    _require_creator(db, p)
    s = db.scalar(select(TestSeries).where(TestSeries.client_request_id == idempotency_key))
    if s and s.created_by != p.staff_id:
        raise api_error(409, "IDEMPOTENCY_KEY_REUSED", "This request key belongs to another user's test.")
    if not s:
        daily._provider(db, p, body.audience.provider_id)
        _eligible, problems, _warnings = _problems(db, p, body.audience, body.subjects)
        _future, schedule_problems = _schedule(body, utcnow())
        if problems or schedule_problems:
            raise api_error_detail(422, "TEST_INVALID", "The Weekly Test was not created.", errors=problems + schedule_problems)
        a = body.audience
        s = TestSeries(provider_id=a.provider_id, branch_id=a.branch_ids[0], name=body.name, description=body.description, month=body.month,
                       weekdays=sorted(body.weekdays), start_time=body.start_time, timezone=body.timezone, window_minutes=body.window_minutes,
                       duration_minutes=body.duration_minutes, generate_lead_minutes=body.generate_lead_minutes, marking_mode=body.marking_mode,
                       marks_correct=body.marks_correct, marks_wrong=body.marks_wrong, exam_type=body.exam_type, target_class=body.target_class,
                       audience=a.model_dump(mode="json"), subjects=[x.model_dump(mode="json") for x in body.subjects],
                       client_request_id=idempotency_key, created_by=p.staff_id)
        db.add(s)
        try:
            db.commit()
        except IntegrityError:          # a concurrent request with the same key won
            db.rollback()
            s = db.scalar(select(TestSeries).where(TestSeries.client_request_id == idempotency_key))
            if not s or s.created_by != p.staff_id:
                raise api_error(409, "IDEMPOTENCY_KEY_REUSED", "This request key belongs to another test.")
        else:
            audit.record(db, "test_series.create", "SUCCESS", actor=p.staff_id, target_type="test_series", target_id=s.series_id,
                         provider_id=s.provider_id, branch_id=s.branch_id, ip=p.ip,
                         details={"month": s.month, "weekdays": s.weekdays, "subjects": len(s.subjects)})
    if s.status == "ACTIVE":
        try:
            result = generate_occurrences(db, s, p.staff_id)
        except SeriesGenerationError as exc:
            raise api_error_detail(502, "GENERATION_FAILED", "The Weekly Test was saved but not every test could be scheduled. Retry "
                                   "'Generate missing tests'.", error=str(exc), series_id=str(s.series_id))
    else:
        result = {"created": [], "existing": [], "skipped_past": []}
    return {**_out(db, s, with_tests=True), "generation": result}


@router.post("/test-series/{series_id}/generate")
def generate_series(series_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Schedule the occurrences that are still missing (retry after a failure). Existing tests are returned unchanged."""
    _require_creator(db, p)
    s = _series(db, p, series_id, manage=True)
    _eligible, problems, _w = _problems(db, p, _audience(s), [SeriesSubjectIn.model_validate(x) for x in s.subjects])
    if problems:
        raise api_error_detail(422, "TEST_INVALID", "The Weekly Test can no longer be scheduled as configured.", errors=problems)
    try:
        result = generate_occurrences(db, s, p.staff_id)
    except SeriesGenerationError as exc:
        status = 409 if s.status != "ACTIVE" else 502
        raise api_error_detail(status, "GENERATION_FAILED", str(exc), series_id=str(series_id))
    return {**_out(db, s, with_tests=True), "generation": result}


@router.post("/test-series/{series_id}/cancel")
def cancel_series(series_id: UUID, body: ReasonIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Stop the remaining occurrences: plans whose exam has not been generated become INACTIVE. Exams already generated, attempts and
    results are unchanged."""
    _require_creator(db, p)
    reason = need_reason(body.reason)
    s = db.scalar(select(TestSeries).where(TestSeries.series_id == series_id).with_for_update())
    if not s:
        raise api_error(404, "NOT_FOUND", "Weekly Test not found.")
    s = _series(db, p, series_id, manage=True)
    if s.status != "ACTIVE":
        raise api_error(409, "INVALID_TRANSITION", "This Weekly Test is already cancelled.")
    stopped = 0
    for plan in _series_plans(db, s):
        if plan.status == "ACTIVE" and not db.scalar(select(Exam.exam_id).where(Exam.plan_id == plan.plan_id).limit(1)):
            plan.status = "INACTIVE"
            stopped += 1
    s.status, s.cancelled_at, s.cancel_reason = "CANCELLED", utcnow(), reason
    db.commit()
    audit.record(db, "test_series.cancel", "SUCCESS", actor=p.staff_id, target_type="test_series", target_id=s.series_id,
                 provider_id=s.provider_id, branch_id=s.branch_id, reason=reason, ip=p.ip, details={"stopped": stopped})
    return {**_out(db, s, with_tests=True), "stopped": stopped}


@router.get("/test-series")
def list_series(provider_id: UUID | None = None, page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal),
                db: Session = Depends(get_db)):
    if not (p.has_perm(PERM) or p.has_perm("exam.read")):
        p.require(db, PERM)
    stmt = select(TestSeries)
    if provider_id:
        stmt = stmt.where(TestSeries.provider_id == provider_id)
    provs = daily.reader_provider_ids(p)
    if provs is not None:
        stmt = stmt.where(TestSeries.provider_id.in_(provs or [None]))
    visible = [s for s in db.scalars(stmt.order_by(TestSeries.created_at.desc())) if _visible(db, p, s)]
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    return {"items": [_out(db, s) for s in visible[(page - 1) * page_size: page * page_size]], "total": len(visible), "page": page,
            "page_size": page_size}


@router.get("/test-series/{series_id}")
def get_series(series_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.has_perm(PERM) or p.has_perm("exam.read")):
        p.require(db, PERM)
    return _out(db, _series(db, p, series_id), with_tests=True)
