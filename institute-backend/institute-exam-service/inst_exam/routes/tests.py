"""Test -> Create Test: one guided workflow for Super Admin, Admin, Provider Admin and Teacher (permission `test.create`).

Order: audience -> name -> subjects -> metadata/schedule -> question paper + answer key per subject (paper service upload, all or
nothing) -> review -> publish. This module owns the audience, the checks and the publication; it reuses the exam plan, the generator,
the delivery outbox (and its student notifications) and the existing exam/result endpoints.

Audience (TARGETED, always within ONE provider) is the UNION of
  * whole branches  (every active batch member of the branch, the BRANCH rule),
  * batches         (members of the selected batches, each in its own branch),
  * students        (individually listed global Student IDs with an active enrollment in a selected branch).
Individual students SUPPLEMENT the batches; nobody is assigned twice. The server resolves the final list; the client's list is never
trusted.

Scope: platform roles - any provider; provider admins - their providers; teachers - only branches, batches and subjects of their
teaching assignments (a whole branch only with a branch-wide assignment, a student only from a batch/branch they teach).

Publication (POST /tests, Idempotency-Key required): validates everything again, creates the plan (unpublished) with its audience and
exact PUBLISHED paper versions, generates the exam (assignments written in the same transaction as the exam), and publishes it only if
EVERY subject resolved; delivery to the Student services and the students' notifications then go through the existing retried outbox.
A retry with the same key returns the same plan/exam; a reduced (PARTIAL) exam is cancelled and reported, never published."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.clients.student_client import StudentClient, StudentServiceError, get_student_client
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.core.taxonomy import Label
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import (
    Exam, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanBranch, ExamPlanStudent, ExamPlanSubject, ExamVersion,
    StudentExamAssignment,
)
from institute_common.models.org import Batch, Branch, Enrollment, EnrollmentBatch, Provider
from institute_common.models.papers import Paper, PaperUpload, PaperVersion
from institute_common.util import Strict
from inst_exam.routes.exams import api_error_detail
from inst_exam.services import exam_generation as gen

router = APIRouter(prefix="/api/v1", tags=["Tests"])
PERM = "test.create"


# ------------------------------------------------------------------ schemas

class TestAudienceIn(Strict):
    provider_id: UUID
    branch_ids: list[UUID] = Field(min_length=1, max_length=20)          # branches the test is for (scope of the batches)
    whole_branch_ids: list[UUID] = Field(default_factory=list, max_length=20)   # of those, entire branches
    batch_ids: list[UUID] = Field(default_factory=list, max_length=100)
    student_ids: list[str] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def rules(self):
        for name in ("branch_ids", "whole_branch_ids", "batch_ids", "student_ids"):
            v = getattr(self, name)
            if len(set(v)) != len(v):
                raise ValueError(f"{name} lists an entry twice")
        if not set(self.whole_branch_ids) <= set(self.branch_ids):
            raise ValueError("an entire branch must also be one of the selected branches")
        if not (self.whole_branch_ids or self.batch_ids or self.student_ids):
            raise ValueError("choose at least one entire branch, batch or student")
        return self


class TestSubjectIn(Strict):
    subject_id: UUID
    paper_version_id: UUID | None = None   # required to publish; the validated (imported) paper version of this subject


class TestDraftIn(Strict):
    audience: TestAudienceIn
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    subjects: list[TestSubjectIn] = Field(min_length=1, max_length=10)
    duration_minutes: int = Field(gt=0, le=720)
    start_at: AwareDatetime
    end_at: AwareDatetime
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

    @model_validator(mode="after")
    def schedule(self):
        if self.end_at <= self.start_at:
            raise ValueError("the window must close after it opens")
        if self.end_at <= datetime.now(timezone.utc):
            raise ValueError("the window must close in the future")
        if len({s.subject_id for s in self.subjects}) != len(self.subjects):
            raise ValueError("a subject is selected twice")
        if self.marking_mode == "UNIFORM" and (self.marks_correct is None or self.marks_wrong is None):
            raise ValueError("uniform marking needs marks for a correct and for a wrong answer")
        if self.marking_mode == "PER_QUESTION" and (self.marks_correct is not None or self.marks_wrong is not None):
            raise ValueError("per-question marking takes the marks from the paper")
        return self


# ------------------------------------------------------------------ scope

def _provider(db: Session, p: Principal, provider_id: UUID) -> Provider:
    prov = db.get(Provider, provider_id)
    if not prov or prov.deleted_at is not None or not p.can(PERM, provider_id=provider_id) and not any(
            p.can(PERM, provider_id=provider_id, branch_id=b) for (b,) in db.execute(select(Branch.branch_id).where(Branch.provider_id == provider_id))):
        raise api_error(404, "NOT_FOUND", "Provider not found.")
    return prov


def _manager(p: Principal, provider_id: UUID, branch_id: UUID) -> bool:
    """test.create through a non-teacher role (platform, provider admin) - i.e. the whole branch."""
    return p.can(PERM, exclude_roles=(Role.TEACHER, Role.BRANCH_ADMIN), provider_id=provider_id, branch_id=branch_id)


def _teaching(p: Principal, provider_id: UUID, branch_id: UUID):
    return [t for t in p.teaching if t.provider_id == provider_id and t.branch_id == branch_id]


def branch_scope(db: Session, p: Principal, provider_id: UUID) -> list[dict]:
    """Branches, batches and subjects the caller may target, per branch."""
    out = []
    branches = db.scalars(select(Branch).where(Branch.provider_id == provider_id, Branch.status == "ACTIVE", Branch.deleted_at.is_(None))
                          .order_by(Branch.name)).all()
    offered = db.execute(select(Subject.subject_id, Subject.name).join(ProviderSubject, ProviderSubject.subject_id == Subject.subject_id)
                         .where(ProviderSubject.provider_id == provider_id, Subject.status == "ACTIVE").order_by(Subject.name)).all()
    for br in branches:
        batches = db.scalars(select(Batch).where(Batch.branch_id == br.branch_id, Batch.status == "ACTIVE").order_by(Batch.name)).all()
        if _manager(p, provider_id, br.branch_id):
            subjects, allowed_batches, whole = offered, batches, True
        else:
            rows = _teaching(p, provider_id, br.branch_id)
            if not rows:
                continue
            taught = {t.subject_id for t in rows}
            subjects = [s for s in offered if s[0] in taught]
            whole = any(t.batch_id is None for t in rows)
            allowed_batches = [b for b in batches if whole or any(t.batch_id == b.batch_id for t in rows)]
        out.append({"branch_id": br.branch_id, "name": br.name, "whole_branch_allowed": whole,
                    "batches": [{"batch_id": b.batch_id, "name": b.name} for b in allowed_batches],
                    "subjects": [{"subject_id": sid, "name": name} for sid, name in subjects]})
    return out


def authorize(db: Session, p: Principal, a: TestAudienceIn, subject_ids: list[UUID]) -> list[dict]:
    """Every target and subject against the caller's scope. Returns problems (empty = authorized). Unknown and unauthorized look
    the same (no probing of other providers' data)."""
    problems: list[dict] = []
    scope = {s["branch_id"]: s for s in branch_scope(db, p, a.provider_id)}
    for b in a.branch_ids:
        if b not in scope:
            problems.append({"field": "branch_ids", "value": str(b), "message": "This branch is not available to you."})
    for b in a.whole_branch_ids:
        if b in scope and not scope[b]["whole_branch_allowed"]:
            problems.append({"field": "whole_branch_ids", "value": scope[b]["name"],
                             "message": "You may target only the batches you teach in this branch, not the entire branch."})
    allowed_batches = {x["batch_id"]: b for b, s in scope.items() if b in a.branch_ids for x in s["batches"]}
    for bid in a.batch_ids:
        if bid not in allowed_batches:
            problems.append({"field": "batch_ids", "value": str(bid), "message": "This batch is not in a selected branch available to you."})
    for sid in subject_ids:
        for b in a.branch_ids:
            if b in scope and not any(x["subject_id"] == sid for x in scope[b]["subjects"]):
                problems.append({"field": "subjects", "value": str(sid), "message": f"This subject is not available to you in {scope[b]['name']}."})
    if a.student_ids:
        reachable = set(student_candidates(db, p, a.provider_id, [b for b in a.branch_ids if b in scope]).keys())
        for sid in a.student_ids:
            if sid not in reachable:
                problems.append({"field": "student_ids", "value": sid,
                                 "message": "This student has no active enrollment you manage in the selected branches."})
    return problems


def student_candidates(db: Session, p: Principal, provider_id: UUID, branch_ids: list[UUID]) -> dict[str, dict]:
    """Students the caller may target individually: ACTIVE enrollments in the given branches (teachers: in batches they teach,
    or anywhere in a branch they teach as a whole). One entry per global student, listing their enrollments' branches/batches."""
    out: dict[str, dict] = {}
    if not branch_ids:
        return out
    names = dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_(branch_ids))).all())
    rows = db.execute(select(Enrollment.student_id, Enrollment.enrollment_id, Enrollment.branch_id, EnrollmentBatch.batch_id, Batch.name)
                      .outerjoin(EnrollmentBatch, EnrollmentBatch.enrollment_id == Enrollment.enrollment_id)
                      .outerjoin(Batch, Batch.batch_id == EnrollmentBatch.batch_id)
                      .where(Enrollment.provider_id == provider_id, Enrollment.status == "ACTIVE", Enrollment.branch_id.in_(branch_ids))
                      .order_by(Enrollment.student_id)).all()
    for sid, _eid, br, bid, bname in rows:
        if not _manager(p, provider_id, br):
            teach = _teaching(p, provider_id, br)
            if not (any(t.batch_id is None for t in teach) or (bid and any(t.batch_id == bid for t in teach))):
                continue
        e = out.setdefault(sid, {"student_id": sid, "branches": set(), "batches": set()})
        e["branches"].add(names.get(br, ""))
        if bname:
            e["batches"].add(bname)
    return out


def resolve(db: Session, a: TestAudienceIn) -> list[tuple[str, str, UUID | None]]:
    return gen.resolve_audience(db, a.provider_id, a.branch_ids[0], "TARGETED", a.batch_ids, a.student_ids, branch_ids=a.whole_branch_ids)


def _names(sc: StudentClient, ids: list[str]) -> dict:
    try:
        return sc.get_many(ids[:500])
    except StudentServiceError:
        return {}


# ------------------------------------------------------------------ endpoints

@router.get("/tests/scope")
def test_scope(provider_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """What the caller may target and teach in this provider (drives the Audience and Subjects steps)."""
    if not p.has_perm(PERM):
        p.require(db, PERM)
    prov = _provider(db, p, provider_id)
    return {"provider_id": prov.provider_id, "provider_name": prov.name, "branches": branch_scope(db, p, provider_id)}


@router.get("/tests/students")
def test_students(provider_id: UUID, branch_ids: list[UUID] = Query(...), q: str | None = Query(default=None, max_length=100),
                  p: Principal = Depends(get_principal), db: Session = Depends(get_db), sc: StudentClient = Depends(get_student_client)):
    """Students who may be targeted individually in the selected branches, identified by global Student ID, name and enrollment."""
    if not p.has_perm(PERM):
        p.require(db, PERM)
    _provider(db, p, provider_id)
    allowed = {s["branch_id"] for s in branch_scope(db, p, provider_id)}
    cands = student_candidates(db, p, provider_id, [b for b in branch_ids if b in allowed])
    names = _names(sc, list(cands))
    term = (q or "").strip().lower()
    items = []
    for sid, c in cands.items():
        s = names.get(sid)
        name = f"{s.first_name} {s.last_name}".strip() if s else None
        if term and term not in sid.lower() and term not in (name or "").lower():
            continue
        items.append({"student_id": sid, "name": name, "branches": sorted(c["branches"]), "batches": sorted(c["batches"])})
    return {"items": items[:200], "total": len(items)}


@router.post("/tests/audience")
def test_audience(body: TestAudienceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                  sc: StudentClient = Depends(get_student_client)):
    """Server-side resolution of the audience: who would be assigned, and why a selection is not allowed."""
    if not p.has_perm(PERM):
        p.require(db, PERM)
    _provider(db, p, body.provider_id)
    problems = authorize(db, p, body, [])
    if problems:
        raise api_error_detail(422, "AUDIENCE_INVALID", "Some selections are not allowed.", errors=problems)
    rows = resolve(db, body)
    missing = [sid for sid in body.student_ids if sid not in {r[0] for r in rows}]
    names = _names(sc, [r[0] for r in rows])
    return {"eligible_count": len(rows),
            "students": [{"student_id": sid, "name": (f"{names[sid].first_name} {names[sid].last_name}" if sid in names else None)}
                         for sid, _e, _b in rows[:300]],
            "not_assigned": [{"student_id": sid, "reason": "No active batch membership/enrollment in the selected branches."} for sid in missing]}


def _check(db: Session, p: Principal, body: TestDraftIn) -> tuple[list[tuple[str, str, UUID | None]], list[dict]]:
    if not p.has_perm(PERM):
        p.require(db, PERM)
    _provider(db, p, body.audience.provider_id)
    problems = authorize(db, p, body.audience, [s.subject_id for s in body.subjects])
    rows = [] if problems else resolve(db, body.audience)
    if not problems and not rows:
        problems.append({"field": "audience", "value": None, "message": "Nobody is eligible: the selected batches/branches have no active students."})
    return rows, problems


@router.post("/tests/check")
def check_test(body: TestDraftIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Validates a draft exactly as publication would, without the papers (each step of the wizard calls it). Writes nothing."""
    rows, problems = _check(db, p, body)
    if problems:
        raise api_error_detail(422, "TEST_INVALID", "The test cannot be created yet.", errors=problems)
    return {"ok": True, "eligible_count": len(rows)}


def _paper_problems(db: Session, body: TestDraftIn) -> list[dict]:
    out = []
    for s in body.subjects:
        if not s.paper_version_id:
            out.append({"field": "subjects", "value": str(s.subject_id), "message": "Upload and validate this subject's question paper and answer key."})
            continue
        v = db.get(PaperVersion, s.paper_version_id)
        paper = db.get(Paper, v.paper_id) if v else None
        if not paper or paper.provider_id != body.audience.provider_id or paper.subject_id != s.subject_id:
            out.append({"field": "subjects", "value": str(s.subject_id), "message": "The paper does not belong to this subject and provider."})
        elif v.status != "PUBLISHED":
            out.append({"field": "subjects", "value": str(s.subject_id), "message": f"The paper version is {v.status}; it must be approved and published."})
        elif not db.scalar(select(PaperUpload.upload_id).where(PaperUpload.paper_version_id == v.paper_version_id, PaperUpload.status == "VALIDATED",
                                                               PaperUpload.answer_key_file_id.is_not(None))):
            out.append({"field": "subjects", "value": str(s.subject_id),
                        "message": "This paper was not imported together with a separate answer key, which Create Test requires."})
    return out


def _plan_summary(db: Session, plan: ExamPlan) -> dict:
    exam = db.scalar(select(Exam).where(Exam.plan_id == plan.plan_id).order_by(Exam.created_at.desc()).limit(1))
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam.exam_id).order_by(ExamVersion.version_no.desc()).limit(1)) if exam else None
    job = db.scalar(select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan.plan_id).order_by(ExamGenerationJob.started_at.desc()).limit(1))
    ob = db.scalar(select(ExamOutbox).where(ExamOutbox.exam_version_id == ev.exam_version_id, ExamOutbox.operation == "PUBLISH")) if ev else None
    subjects = db.execute(select(Subject.name).join(ExamPlanSubject, ExamPlanSubject.subject_id == Subject.subject_id)
                          .where(ExamPlanSubject.plan_id == plan.plan_id).order_by(ExamPlanSubject.position)).scalars().all()
    branches = db.execute(select(Branch.name).join(ExamPlanBranch, ExamPlanBranch.branch_id == Branch.branch_id).where(ExamPlanBranch.plan_id == plan.plan_id)).scalars().all()
    batches = db.execute(select(Batch.name).join(ExamPlanBatch, ExamPlanBatch.batch_id == Batch.batch_id).where(ExamPlanBatch.plan_id == plan.plan_id)).scalars().all()
    students = db.scalar(select(func.count()).select_from(ExamPlanStudent).where(ExamPlanStudent.plan_id == plan.plan_id)) or 0
    assigned = db.scalar(select(func.count()).select_from(StudentExamAssignment).where(StudentExamAssignment.exam_id == exam.exam_id)) if exam else 0
    if exam and exam.status == "PUBLISHED":
        state = "PUBLISHED" if (ob and ob.status == "SENT") else "PUBLISHED_DELIVERY_PENDING" if (not ob or ob.status == "PENDING") else "DELIVERY_FAILED"
    elif exam and exam.status == "CANCELLED":
        state = "CANCELLED"
    elif job and job.status == "FAILED":
        state = "PUBLICATION_FAILED"
    elif job and job.status == "RUNNING":
        state = "PUBLISHING"
    else:
        state = "NOT_PUBLISHED"
    prov, home = db.get(Provider, plan.provider_id), db.get(Branch, plan.branch_id)
    return {"plan_id": plan.plan_id, "exam_id": exam.exam_id if exam else None, "exam_version_id": ev.exam_version_id if ev else None,
            "name": plan.name, "state": state, "exam_status": exam.status if exam else None, "delivery_status": ob.status if ob else None,
            "job_status": job.status if job else None, "error": (job.error if job and job.status != "SUCCEEDED" else None) or (exam.cancelled_reason if exam else None),
            "provider_id": plan.provider_id, "provider_name": prov.name if prov else None, "home_branch": home.name if home else None,
            "start_at": ev.start_at if ev else plan.start_at, "end_at": ev.end_at if ev else plan.end_at, "duration_minutes": plan.duration_minutes,
            "subjects": list(subjects), "audience": {"type": plan.audience_type, "branches": sorted(branches), "batches": sorted(batches), "students": students},
            "assigned_students": assigned or 0, "question_count": ev.total_questions if ev else None,
            "total_marks": str(ev.total_marks) if ev else None, "marking": ev.settings.get("marking") if ev and ev.settings else None,
            "created_by": plan.created_by, "created_at": plan.created_at}


@router.post("/tests", status_code=201)
def publish_test(body: TestDraftIn, idempotency_key: str = Header(min_length=8, max_length=100), p: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    """Create and publish the test (see module docstring). The same Idempotency-Key always returns the same plan and exam."""
    existing = db.scalar(select(ExamPlan).where(ExamPlan.client_request_id == idempotency_key))
    if existing:
        if existing.created_by != p.staff_id:
            raise api_error(409, "IDEMPOTENCY_KEY_REUSED", "This request key belongs to another user's test.")
        plan = existing
    else:
        rows, problems = _check(db, p, body)
        problems += _paper_problems(db, body)
        if problems:
            raise api_error_detail(422, "TEST_INVALID", "The test was not published.", errors=problems)
        a = body.audience
        plan = ExamPlan(provider_id=a.provider_id, branch_id=a.branch_ids[0], name=body.name, description=body.description,
                        duration_minutes=body.duration_minutes, start_at=body.start_at, end_at=body.end_at, auto_publish=False, status="ACTIVE",
                        audience_type="TARGETED", marking_mode=body.marking_mode, marks_correct=body.marks_correct, marks_wrong=body.marks_wrong,
                        exam_type=body.exam_type, target_class=body.target_class, client_request_id=idempotency_key, created_by=p.staff_id)
        db.add(plan)
        try:
            db.flush()
            for b in a.whole_branch_ids:
                db.add(ExamPlanBranch(plan_id=plan.plan_id, branch_id=b))
            for bid in a.batch_ids:
                db.add(ExamPlanBatch(plan_id=plan.plan_id, batch_id=bid))
            for sid in a.student_ids:
                db.add(ExamPlanStudent(plan_id=plan.plan_id, student_id=sid))
            for pos, s in enumerate(body.subjects, start=1):
                db.add(ExamPlanSubject(plan_id=plan.plan_id, position=pos, subject_id=s.subject_id, paper_version_id=s.paper_version_id))
            db.commit()
        except IntegrityError:            # a concurrent request with the same key won
            db.rollback()
            plan = db.scalar(select(ExamPlan).where(ExamPlan.client_request_id == idempotency_key))
            if not plan or plan.created_by != p.staff_id:
                raise api_error(409, "IDEMPOTENCY_KEY_REUSED", "This request key belongs to another test.")
        audit.record(db, "test.create", "SUCCESS", actor=p.staff_id, target_type="exam_plan", target_id=plan.plan_id,
                     provider_id=plan.provider_id, branch_id=plan.branch_id, ip=p.ip, details={"eligible": len(rows), "subjects": len(body.subjects)})
    if plan.status == "ACTIVE":
        try:
            gen.generate(db, plan.plan_id, actor=p.staff_id)
        except gen.PlanNotGeneratable as exc:
            raise api_error(409, "PUBLICATION_FAILED", str(exc))
        db.expire_all()
        # Decide from the exam itself (not only the job's final status, which a failure after the exam was committed can leave FAILED):
        # a COMPLETE unpublished exam is published - status and delivery queued in one transaction; a reduced one is cancelled.
        exam = db.scalar(select(Exam).where(Exam.plan_id == plan.plan_id).order_by(Exam.created_at.desc()).limit(1))
        ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam.exam_id).order_by(ExamVersion.version_no.desc()).limit(1)) if exam else None
        if exam and ev and exam.status == "SCHEDULED":
            if not ev.missing_subjects and ev.total_questions > 0:
                exam.status, ev.published_at = "PUBLISHED", utcnow()
                db.add(ExamOutbox(exam_version_id=ev.exam_version_id, operation="PUBLISH"))
                db.commit()
                audit.record(db, "test.publish", "SUCCESS", actor=p.staff_id, target_type="exam", target_id=exam.exam_id,
                             provider_id=plan.provider_id, branch_id=plan.branch_id, ip=p.ip)
            else:
                exam.status, exam.cancelled_reason = "CANCELLED", "Publication refused: not every subject's paper was available."
                plan.status = "INACTIVE"
                db.commit()
                audit.record(db, "test.publish", "FAILURE", actor=p.staff_id, target_type="exam_plan", target_id=plan.plan_id,
                             provider_id=plan.provider_id, branch_id=plan.branch_id, ip=p.ip, details={"missing": ev.missing_subjects})
    summary = _plan_summary(db, plan)
    if summary["state"] not in ("PUBLISHED", "PUBLISHED_DELIVERY_PENDING"):
        raise api_error_detail(409, "PUBLICATION_FAILED", summary["error"] or "The test was not published.", test=_jsonable(summary))
    return summary


def _jsonable(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, (UUID, datetime, Decimal)) else v) for k, v in d.items()}


@router.get("/tests")
def list_tests(provider_id: UUID | None = None, page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Scheduled tests the caller may see: platform roles all, provider admins their providers, teachers the tests they created or
    whose every subject they teach in the test's branch."""
    if not p.has_perm(PERM):
        p.require(db, PERM)
    stmt = select(ExamPlan).where(ExamPlan.global_test_id.is_(None))
    if provider_id:
        stmt = stmt.where(ExamPlan.provider_id == provider_id)
    provs = p.scope_provider_ids(PERM)
    if provs is not None:
        stmt = stmt.where(ExamPlan.provider_id.in_(provs or [None]))
    plans = db.scalars(stmt.order_by(ExamPlan.created_at.desc())).all()
    visible = []
    for plan in plans:
        if _manager(p, plan.provider_id, plan.branch_id) or plan.created_by == p.staff_id:
            visible.append(plan)
            continue
        subjects = db.scalars(select(ExamPlanSubject.subject_id).where(ExamPlanSubject.plan_id == plan.plan_id)).all()
        if subjects and all(p.can(PERM, provider_id=plan.provider_id, branch_id=plan.branch_id, subject_id=s) for s in subjects):
            visible.append(plan)
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    chunk = visible[(page - 1) * page_size: page * page_size]
    return {"items": [_plan_summary(db, x) for x in chunk], "total": len(visible), "page": page, "page_size": page_size}
