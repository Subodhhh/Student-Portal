"""Test -> Daily Test (formerly Create Test): one guided workflow for Super Admin, Admin, Provider Admin and Branch Admin (permission
`test.create`; a Branch Admin only for their own branch). Teachers do not create tests: they see the scheduled tests that target a
student they teach one of the test's subjects to (`exam.read`, services/teacher_scope.py). The Weekly Test (routes/test_series.py) reuses this module's audience, scope and paper rules.

Order: audience -> name -> subjects -> metadata/schedule -> per subject either a NEW question paper + answer key (paper service upload,
all or nothing) or an approved paper already in the bank -> review -> publish. This module owns the audience, the checks and the
publication; it reuses the exam plan, the generator, the delivery outbox (and its student notifications) and the existing exam/result
endpoints.

Audience (TARGETED, always within ONE provider) is the UNION of
  * whole branches  (every active batch member of the branch, the BRANCH rule),
  * batches         (members of the selected batches, each in its own branch),
  * students        (individually listed global Student IDs with an active enrollment in a selected branch).
Individual students SUPPLEMENT the batches; nobody is assigned twice. The server resolves the final list; the client's list is never
trusted.

Scope: platform roles - any provider; provider admins - their providers; branch admins - their branch. (The teaching-assignment
branch of the scope rules below remains for any role that holds test.create through teaching assignments; none does today.)

Publication (POST /tests, Idempotency-Key required): validates everything again, creates the plan (unpublished) with its audience and
exact PUBLISHED paper versions, generates the exam (assignments written in the same transaction as the exam), and publishes it only if
EVERY subject resolved; delivery to the Student services and the students' notifications then go through the existing retried outbox.
A retry with the same key returns the same plan/exam; a reduced (PARTIAL) exam is cancelled and reported, never published."""
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.clients.student_client import StudentClient, StudentServiceError, get_student_client
from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.core.taxonomy import Label
from institute_common.core.class_levels import TargetClass, normalize_class
from institute_common.database import get_db, utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import (
    Exam, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanBranch, ExamPlanStudent, ExamPlanSubject, ExamVersion,
    StudentExamAssignment,
)
from institute_common.models.org import Batch, Branch, Enrollment, EnrollmentBatch, Provider
from institute_common.models.papers import Paper, PaperShare, PaperUpload, PaperVersion
from institute_common.util import Strict
from inst_exam.routes.exams import api_error_detail
from inst_exam.services import exam_generation as gen
from inst_exam.services import teacher_scope
from inst_exam.services.test_policy import can_create_test

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
    target_class: TargetClass = None

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

def _provider(db: Session, p: Principal, provider_id: UUID, perm: str = PERM) -> Provider:
    prov = db.get(Provider, provider_id)
    if not prov or prov.deleted_at is not None or not p.can(perm, provider_id=provider_id) and not any(
            p.can(perm, provider_id=provider_id, branch_id=b) for (b,) in db.execute(select(Branch.branch_id).where(Branch.provider_id == provider_id))):
        raise api_error(404, "NOT_FOUND", "Provider not found.")
    return prov


def _manager(p: Principal, provider_id: UUID, branch_id: UUID, perm: str = PERM) -> bool:
    """`perm` (default test.create) through an admin role for this branch (platform, provider admin, or the branch's own admin) - the
    whole branch."""
    return p.can(perm, exclude_roles=(Role.TEACHER,), provider_id=provider_id, branch_id=branch_id)


def require_test_type(db: Session, p: Principal, test_type: str) -> None:
    """Which roles may create which test type (services/test_policy.py): Global Tests are platform-only, Daily and Weekly Tests are
    for platform, provider and branch admins. Checked in addition to the scoped permission, never instead of it."""
    if not any(can_create_test(role.value, test_type) for role in p.roles):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": PERM, "test_type": test_type})
        raise api_error(403, "FORBIDDEN", f"Your role cannot create {test_type.title()} Tests.")


def _teaching(p: Principal, provider_id: UUID, branch_id: UUID):
    return [t for t in p.teaching if t.provider_id == provider_id and t.branch_id == branch_id]


def branch_scope(db: Session, p: Principal, provider_id: UUID, perm: str = PERM) -> list[dict]:
    """Branches, batches and subjects the caller may target, per branch (admins of a branch: all of it; teachers: their teaching
    assignments)."""
    out = []
    branches = db.scalars(select(Branch).where(Branch.provider_id == provider_id, Branch.status == "ACTIVE", Branch.deleted_at.is_(None))
                          .order_by(Branch.name)).all()
    offered = db.execute(select(Subject.subject_id, Subject.name).join(ProviderSubject, ProviderSubject.subject_id == Subject.subject_id)
                         .where(ProviderSubject.provider_id == provider_id, Subject.status == "ACTIVE").order_by(Subject.name)).all()
    for br in branches:
        batches = db.scalars(select(Batch).where(Batch.branch_id == br.branch_id, Batch.status == "ACTIVE").order_by(Batch.name)).all()
        if _manager(p, provider_id, br.branch_id, perm):
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


def authorize(db: Session, p: Principal, a: TestAudienceIn, subject_ids: list[UUID], perm: str = PERM) -> list[dict]:
    """Every target and subject against the caller's scope. Returns problems (empty = authorized). Unknown and unauthorized look
    the same (no probing of other providers' data)."""
    problems: list[dict] = []
    scope = {s["branch_id"]: s for s in branch_scope(db, p, a.provider_id, perm)}
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
        reachable = set(student_candidates(db, p, a.provider_id, [b for b in a.branch_ids if b in scope], perm).keys())
        for sid in a.student_ids:
            if sid not in reachable:
                problems.append({"field": "student_ids", "value": sid,
                                 "message": "This student has no active enrollment you manage in the selected branches."})
    return problems


def student_candidates(db: Session, p: Principal, provider_id: UUID, branch_ids: list[UUID], perm: str = PERM) -> dict[str, dict]:
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
        if not _manager(p, provider_id, br, perm):
            teach = _teaching(p, provider_id, br)
            if not (any(t.batch_id is None for t in teach) or (bid and any(t.batch_id == bid for t in teach))):
                continue
        e = out.setdefault(sid, {"student_id": sid, "branches": set(), "batches": set()})
        e["branches"].add(names.get(br, ""))
        if bname:
            e["batches"].add(bname)
    return out


def resolve(db: Session, a: TestAudienceIn, subject_ids=(), class_level: str | None = None,
            report: dict | None = None) -> list[tuple[str, str, UUID | None]]:
    """`subject_ids`: the test's subjects; batch-based selections then reach only students who take all of them in that batch.
    `class_level`: the test's Class code (core/class_levels); batch-based selections then reach only enrollments of that Class."""
    return gen.resolve_audience(db, a.provider_id, a.branch_ids[0], "TARGETED", a.batch_ids, a.student_ids, branch_ids=a.whole_branch_ids,
                                subject_ids=subject_ids, class_level=class_level, report=report)


def class_note(report: dict, rows) -> str:
    """Why students were kept out by the Class, for messages ('' when the Class excluded nobody)."""
    got = {r[0] for r in rows}
    none, other = len(report.get("excluded_no_class", set()) - got), len(report.get("excluded_other_class", set()) - got)
    parts = ([f"{none} without a Class on their enrollment"] if none else []) + ([f"{other} in a different Class"] if other else [])
    return f" The test's Class excluded {' and '.join(parts)}." if parts else ""


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


class TestAudiencePreviewIn(TestAudienceIn):
    subject_ids: list[UUID] = Field(default_factory=list, max_length=20)   # the test's subjects, when already chosen
    target_class: TargetClass = None                                      # the test's Class, when chosen


@router.post("/tests/audience")
def test_audience(body: TestAudiencePreviewIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                  sc: StudentClient = Depends(get_student_client)):
    """Server-side resolution of the audience: who would be assigned, and why a selection is not allowed."""
    if not p.has_perm(PERM):
        p.require(db, PERM)
    _provider(db, p, body.provider_id)
    problems = authorize(db, p, body, [])
    if problems:
        raise api_error_detail(422, "AUDIENCE_INVALID", "Some selections are not allowed.", errors=problems)
    report: dict = {}
    rows = resolve(db, body, body.subject_ids, normalize_class(body.target_class), report)
    missing = [sid for sid in body.student_ids if sid not in {r[0] for r in rows}]
    names = _names(sc, [r[0] for r in rows])
    return {"eligible_count": len(rows),
            "students": [{"student_id": sid, "name": (f"{names[sid].first_name} {names[sid].last_name}" if sid in names else None)}
                         for sid, _e, _b in rows[:300]],
            "not_assigned": [{"student_id": sid, "reason": "No active batch membership/enrollment in the selected branches."} for sid in missing],
            "class_note": class_note(report, rows).strip() or None}


def _check(db: Session, p: Principal, body: TestDraftIn) -> tuple[list[tuple[str, str, UUID | None]], list[dict]]:
    if not p.has_perm(PERM):
        p.require(db, PERM)
    require_test_type(db, p, "DAILY")
    _provider(db, p, body.audience.provider_id)
    problems = authorize(db, p, body.audience, [s.subject_id for s in body.subjects])
    report: dict = {}
    rows = [] if problems else resolve(db, body.audience, [s.subject_id for s in body.subjects], normalize_class(body.target_class), report)
    if not problems and not rows:
        problems.append({"field": "audience", "value": None,
                         "message": "Nobody is eligible: the selected batches/branches have no active students." + class_note(report, rows)})
    return rows, problems


@router.post("/tests/check")
def check_test(body: TestDraftIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Validates a draft exactly as publication would, without the papers (each step of the wizard calls it). Writes nothing."""
    rows, problems = _check(db, p, body)
    if problems:
        raise api_error_detail(422, "TEST_INVALID", "The test cannot be created yet.", errors=problems)
    return {"ok": True, "eligible_count": len(rows)}


def paper_problem(db: Session, p: Principal, provider_id: UUID, subject_id: UUID, paper: Paper | None) -> str | None:
    """Why `paper` cannot be used for `subject_id` in a test of `provider_id` (None = usable). Usable: the provider's own paper the
    caller may read (a branch admin: their branch's papers), a paper another provider shared with it, or a platform paper that is
    not reserved for a Global Test. Unknown and unauthorized look the same."""
    if not paper or paper.subject_id != subject_id:
        return "The paper does not belong to this subject."
    if paper.scope == "GLOBAL":
        if not gen.may_use_platform_paper(db, None, paper, None):
            return "This platform paper belongs to a Global Test; schedule that test instead."
        return None
    if paper.provider_id == provider_id:
        return None if p.can("paper.read", provider_id=provider_id, branch_id=paper.branch_id, subject_id=subject_id) \
            else "The paper is not available to you."
    return None if gen.authorized_source(db, provider_id, paper) else "The paper is not available to you."


def _paper_problems(db: Session, p: Principal, body: TestDraftIn) -> list[dict]:
    out = []
    for s in body.subjects:
        if not s.paper_version_id:
            out.append({"field": "subjects", "value": str(s.subject_id),
                        "message": "Upload this subject's question paper and answer key, or choose an approved paper."})
            continue
        v = db.get(PaperVersion, s.paper_version_id)
        paper = db.get(Paper, v.paper_id) if v else None
        problem = paper_problem(db, p, body.audience.provider_id, s.subject_id, paper)
        if problem:
            out.append({"field": "subjects", "value": str(s.subject_id), "message": problem})
        elif v.status != "PUBLISHED":
            out.append({"field": "subjects", "value": str(s.subject_id), "message": f"The paper version is {v.status}; it must be approved and published."})
        elif _uploaded_without_key(db, v.paper_version_id):
            out.append({"field": "subjects", "value": str(s.subject_id),
                        "message": "This paper was uploaded without a separate answer key, which a Daily Test upload requires."})
    return out


def _uploaded_without_key(db: Session, paper_version_id: UUID) -> bool:
    """An uploaded paper must have come with its separate answer key; a paper built from bank questions (no upload) carries its
    answers in the questions themselves."""
    uploads = db.scalars(select(PaperUpload).where(PaperUpload.paper_version_id == paper_version_id, PaperUpload.status == "VALIDATED")).all()
    return bool(uploads) and not any(u.answer_key_file_id for u in uploads)


def test_type(plan: ExamPlan) -> str:
    """The product's three test types: a scheduled Global Test, an occurrence of a Weekly Test, or a Daily Test (every other plan)."""
    return "GLOBAL" if plan.global_test_id else "WEEKLY" if plan.series_id else "DAILY"


def _plan_summary(db: Session, plan: ExamPlan) -> dict:
    return _summaries(db, [plan])[0]


def _summaries(db: Session, plans: list[ExamPlan]) -> list[dict]:
    """Summaries of many plans in a fixed number of bulk queries (independent of how many plans: a remote database answers each
    round trip in tens of milliseconds, so per-plan queries made the Scheduled Tests list take most of a minute)."""
    if not plans:
        return []
    ids = [pl.plan_id for pl in plans]
    exams: dict = {}
    for e in db.scalars(select(Exam).where(Exam.plan_id.in_(ids)).order_by(Exam.created_at)):
        exams[e.plan_id] = e                                                        # latest wins
    exam_ids = [e.exam_id for e in exams.values()]
    versions: dict = {}
    for v in db.scalars(select(ExamVersion).where(ExamVersion.exam_id.in_(exam_ids or [None])).order_by(ExamVersion.version_no)):
        versions[v.exam_id] = v
    ev_ids = [v.exam_version_id for v in versions.values()]
    outboxes = {o.exam_version_id: o for o in db.scalars(select(ExamOutbox).where(ExamOutbox.exam_version_id.in_(ev_ids or [None]), ExamOutbox.operation == "PUBLISH"))}
    jobs: dict = {}
    for j in db.scalars(select(ExamGenerationJob).where(ExamGenerationJob.plan_id.in_(ids)).order_by(ExamGenerationJob.started_at)):
        jobs[j.plan_id] = j
    subjects: dict = {}
    for pid, name in db.execute(select(ExamPlanSubject.plan_id, Subject.name).join(Subject, Subject.subject_id == ExamPlanSubject.subject_id)
                                .where(ExamPlanSubject.plan_id.in_(ids)).order_by(ExamPlanSubject.position)):
        subjects.setdefault(pid, []).append(name)
    branches: dict = {}
    for pid, name in db.execute(select(ExamPlanBranch.plan_id, Branch.name).join(Branch, Branch.branch_id == ExamPlanBranch.branch_id).where(ExamPlanBranch.plan_id.in_(ids))):
        branches.setdefault(pid, []).append(name)
    batches: dict = {}
    for pid, name in db.execute(select(ExamPlanBatch.plan_id, Batch.name).join(Batch, Batch.batch_id == ExamPlanBatch.batch_id).where(ExamPlanBatch.plan_id.in_(ids))):
        batches.setdefault(pid, []).append(name)
    students = dict(db.execute(select(ExamPlanStudent.plan_id, func.count()).where(ExamPlanStudent.plan_id.in_(ids)).group_by(ExamPlanStudent.plan_id)).all())
    assigned = dict(db.execute(select(StudentExamAssignment.exam_id, func.count()).where(StudentExamAssignment.exam_id.in_(exam_ids or [None]))
                               .group_by(StudentExamAssignment.exam_id)).all())
    providers = dict(db.execute(select(Provider.provider_id, Provider.name).where(Provider.provider_id.in_({pl.provider_id for pl in plans}))).all())
    homes = dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_({pl.branch_id for pl in plans}))).all())
    out = []
    for plan in plans:
        exam, job = exams.get(plan.plan_id), jobs.get(plan.plan_id)
        ev = versions.get(exam.exam_id) if exam else None
        ob = outboxes.get(ev.exam_version_id) if ev else None
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
        out.append({"plan_id": plan.plan_id, "test_type": test_type(plan), "series_id": plan.series_id, "global_test_id": plan.global_test_id,
                    "exam_id": exam.exam_id if exam else None, "exam_version_id": ev.exam_version_id if ev else None,
                    "name": plan.name, "state": state, "exam_status": exam.status if exam else None, "delivery_status": ob.status if ob else None,
                    "job_status": job.status if job else None,
                    "error": (job.error if job and job.status != "SUCCEEDED" else None) or (exam.cancelled_reason if exam else None),
                    "provider_id": plan.provider_id, "provider_name": providers.get(plan.provider_id), "home_branch": homes.get(plan.branch_id),
                    "start_at": ev.start_at if ev else plan.start_at, "end_at": ev.end_at if ev else plan.end_at, "duration_minutes": plan.duration_minutes,
                    "subjects": subjects.get(plan.plan_id, []),
                    "audience": {"type": plan.audience_type, "branches": sorted(branches.get(plan.plan_id, [])), "batches": sorted(batches.get(plan.plan_id, [])),
                                 "students": students.get(plan.plan_id, 0)},
                    "assigned_students": assigned.get(exam.exam_id, 0) if exam else 0, "question_count": ev.total_questions if ev else None,
                    "total_marks": str(ev.total_marks) if ev else None, "marking": ev.settings.get("marking") if ev and ev.settings else None,
                    "created_by": plan.created_by, "created_at": plan.created_at})
    return out


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
        problems += _paper_problems(db, p, body)
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
        # Another request with the same key may be generating right now (job RUNNING): wait for it briefly instead of reporting failure.
        for _ in range(60):
            running = db.scalar(select(ExamGenerationJob.job_id).where(ExamGenerationJob.plan_id == plan.plan_id, ExamGenerationJob.status == "RUNNING"))
            if not running:
                break
            time.sleep(0.25)
            db.expire_all()
        # Decide from the exam itself (not only the job's final status, which a failure after the exam was committed can leave FAILED):
        # a COMPLETE unpublished exam is published - status and delivery queued in one transaction; a reduced one is cancelled.
        # row lock: concurrent publications of the same request wait here, then see the exam already PUBLISHED
        exam = db.scalar(select(Exam).where(Exam.plan_id == plan.plan_id).order_by(Exam.created_at.desc()).limit(1).with_for_update())
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
    if summary["state"] == "PUBLISHING":                # still being generated by a concurrent request: not a failure
        return JSONResponse(status_code=202, content=jsonable_encoder(summary))
    if summary["state"] not in ("PUBLISHED", "PUBLISHED_DELIVERY_PENDING"):
        raise api_error_detail(409, "PUBLICATION_FAILED", summary["error"] or "The test was not published.", test=_jsonable(summary))
    return summary


def _jsonable(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, (UUID, datetime, Decimal)) else v) for k, v in d.items()}


def visible_plans(db: Session, p: Principal, plans: list[ExamPlan]) -> list[ExamPlan]:
    """Plans the caller may see: admins of the plan's branch (platform, provider, branch admin), its creator, and teachers with at
    least one targeted student in their teaching scope for one of the plan's subjects (services/teacher_scope.py)."""
    visible = []
    for plan in plans:
        if _manager(p, plan.provider_id, plan.branch_id) or plan.created_by == p.staff_id:
            visible.append(plan)
        elif p.can("exam.read", provider_id=plan.provider_id, branch_id=plan.branch_id, exclude_roles=(Role.TEACHER,)):
            visible.append(plan)
        elif p.has_role(Role.TEACHER) and p.can("exam.read", provider_id=plan.provider_id) and teacher_scope.plan_students(db, p, plan):
            visible.append(plan)
    return visible


def reader_provider_ids(p: Principal) -> set[UUID] | None:
    """Providers whose tests the caller may list (test.create or exam.read); None = every provider."""
    scopes = [p.scope_provider_ids(PERM), p.scope_provider_ids("exam.read")]
    if any(s is None for s in scopes):
        return None
    return set().union(*scopes)


@router.get("/tests")
def list_tests(provider_id: UUID | None = None, test_type: str | None = Query(default=None, pattern="^(DAILY|WEEKLY|GLOBAL)$"),
               global_test_id: UUID | None = None, page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Scheduled tests of all three types the caller may see (see `visible_plans`), newest first."""
    if not (p.has_perm(PERM) or p.has_perm("exam.read")):
        p.require(db, PERM)
    stmt = select(ExamPlan)
    if test_type == "GLOBAL":
        stmt = stmt.where(ExamPlan.global_test_id.is_not(None))
    elif test_type == "WEEKLY":
        stmt = stmt.where(ExamPlan.series_id.is_not(None))
    elif test_type == "DAILY":
        stmt = stmt.where(ExamPlan.global_test_id.is_(None), ExamPlan.series_id.is_(None))
    if provider_id:
        stmt = stmt.where(ExamPlan.provider_id == provider_id)
    if global_test_id:                                  # the schedules of one Global Test (all of its versions are separate tests)
        stmt = stmt.where(ExamPlan.global_test_id == global_test_id)
    provs = reader_provider_ids(p)
    if provs is not None:
        stmt = stmt.where(ExamPlan.provider_id.in_(provs or [None]))
    visible = visible_plans(db, p, db.scalars(stmt.order_by(ExamPlan.created_at.desc())).all())
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    chunk = visible[(page - 1) * page_size: page * page_size]
    return {"items": _summaries(db, chunk), "total": len(visible), "page": page, "page_size": page_size}


@router.get("/tests/papers")
def test_papers(provider_id: UUID, subject_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Approved papers the caller may use for one subject of a Daily or Weekly Test: the provider's own (within the caller's
    branches), papers shared with the provider, and platform papers that no Global Test reserves. Each lists its latest published
    version, if any (a Weekly Test may name a paper whose next version is still being approved)."""
    if not p.has_perm(PERM):
        p.require(db, PERM)
    _provider(db, p, provider_id)
    papers = db.scalars(select(Paper).where(Paper.subject_id == subject_id, (Paper.provider_id == provider_id) | (Paper.scope == "GLOBAL")
                                            | Paper.paper_id.in_(select(PaperShare.paper_id).where(
                                                PaperShare.shared_with_provider_id == provider_id, PaperShare.status == "ACTIVE")))
                        .order_by(Paper.title)).all()
    items = []
    for paper in papers:
        if paper_problem(db, p, provider_id, subject_id, paper):
            continue
        latest = db.scalar(select(PaperVersion).where(PaperVersion.paper_id == paper.paper_id, PaperVersion.status == "PUBLISHED")
                           .order_by(PaperVersion.version_no.desc()).limit(1))
        if latest and _uploaded_without_key(db, latest.paper_version_id):
            latest = None
        items.append({"paper_id": paper.paper_id, "title": paper.title, "scope": paper.scope,
                      "source": "Platform" if paper.scope == "GLOBAL" else "Own" if paper.provider_id == provider_id else "Shared",
                      "published_version": None if not latest else {
                          "paper_version_id": latest.paper_version_id, "version_no": latest.version_no,
                          "question_count": latest.question_count, "total_marks": str(latest.total_marks)}})
    return {"items": items[:200], "total": len(items)}
