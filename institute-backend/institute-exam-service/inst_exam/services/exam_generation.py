"""Automatic exam generation.

Rules implemented here (all unit-tested):
  * only approved/published paper versions, and only papers the plan's provider owns or has
    been granted through an ACTIVE share;
  * exact version if the plan names one, otherwise the paper's latest PUBLISHED version;
  * a missing/unauthorized subject never blocks the others: the exam is generated from what is
    available and the gaps are recorded and notified;
  * the manifest (exam_version_papers) is immutable, so later approvals, mapping changes or
    new paper versions affect only the NEXT generation, never an existing exam;
  * idempotent per (plan, window start): manual and scheduled generation share ONE canonical run key
    (`canonical_run_key`), backed by the unique (plan_id, run_key) constraints on jobs and exams, so a retry, a second
    request or a concurrent scheduler run returns the same exam and never duplicates exams, assignments or notifications;
    runs recorded under the older "manual:..." / "scheduled:..." keys are recognised for the same start;
  * the audience (batches, a branch, the whole institute or listed students) is resolved from CURRENT active enrollments and
    batch memberships at generation time; each student is assigned once, and a student already holding the same Global Daily
    Test for the same start time (from another plan of the provider) is not assigned it again;
  * the configuration the exam is generated with (marking, fixed policies, audience, source test) is frozen into
    exam_versions.settings next to the manifest, so changing the plan later never changes an existing exam.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.database import utcnow
from institute_common.models.exams import (
    Exam, ExamGenerationEvent, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanStudent, ExamPlanSubject,
    ExamVersion, ExamVersionPaper, GlobalTest, GlobalTestSection, StudentExamAssignment,
)
from institute_common.models.org import Batch, Branch, Enrollment, EnrollmentBatch
from institute_common.models.academics import Subject
from institute_common.models.papers import Paper, PaperShare, PaperVersion
from inst_exam.services.notify import notify, responsible_staff

USABLE_FOR_EXPLICIT = {"APPROVED", "PUBLISHED", "SUPERSEDED"}
STALE_RUNNING = timedelta(minutes=10)


@dataclass
class Resolved:
    slot: ExamPlanSubject
    version: PaperVersion
    paper: Paper


@dataclass
class Resolution:
    resolved: list[Resolved] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)


# Policies the Student services implement today. They are recorded on every exam so the review screen and the exam detail state
# them explicitly; they are not configurable because the Student attempt/result services have exactly this behaviour.
FIXED_POLICIES = {
    "unanswered": "ZERO",                     # an unanswered question scores 0
    "attempts": "ONE_WITH_RESUME",            # one attempt per exam; a disconnected student resumes the same attempt
    "timer": "SHARED_FULL_DURATION",          # one timer across all subjects; starting inside the window gives the full duration
    "results": "AFTER_EVALUATION",            # results appear once the submission is evaluated
    "answer_review": "AFTER_WINDOW_CLOSES",   # correct answers are revealed only when nobody can still start the exam
    "shuffle": "NONE",                        # question order is the paper order
}


def plan_settings(plan: ExamPlan) -> dict:
    marking = {"mode": plan.marking_mode}
    if plan.marking_mode == "UNIFORM":
        marking.update(correct=plan.marks_correct, wrong=plan.marks_wrong)
    return {"marking": marking, "policies": FIXED_POLICIES, "audience_type": plan.audience_type,
            "global_test_id": str(plan.global_test_id) if plan.global_test_id else None,
            "exam_type": plan.exam_type, "target_class": plan.target_class}


def authorized_source(db: Session, plan_provider_id: UUID, paper: Paper) -> bool:
    if paper.scope == "GLOBAL":
        return True   # platform papers are provided to every provider (read/use only; changing them is platform-only)
    if paper.provider_id == plan_provider_id:
        return True
    return db.scalar(select(PaperShare).where(
        PaperShare.paper_id == paper.paper_id, PaperShare.shared_with_provider_id == plan_provider_id,
        PaperShare.status == "ACTIVE")) is not None


def may_use_platform_paper(db: Session, plan_global_test_id: UUID | None, paper: Paper, version_id: UUID | None) -> bool:
    """A platform paper that belongs to a Global Daily Test (any of its versions is a test section) is scheduled only through that
    test's own plan, with exactly the section's version; an ordinary plan cannot reuse it with other marking or duration.
    Platform papers that are in no Global Daily Test are freely usable."""
    if paper.scope != "GLOBAL":
        return True
    sections = db.execute(select(GlobalTestSection.test_id, GlobalTestSection.paper_version_id)
                          .join(PaperVersion, PaperVersion.paper_version_id == GlobalTestSection.paper_version_id)
                          .where(PaperVersion.paper_id == paper.paper_id)).all()
    if not sections:
        return True
    return plan_global_test_id is not None and (plan_global_test_id, version_id) in {(t, v) for t, v in sections}


def resolve_slots(db: Session, plan: ExamPlan) -> Resolution:
    out = Resolution()
    slots = db.scalars(select(ExamPlanSubject).where(ExamPlanSubject.plan_id == plan.plan_id).order_by(ExamPlanSubject.position)).all()
    for slot in slots:
        def miss(reason: str, detail: str, slot: ExamPlanSubject = slot):
            subj = db.get(Subject, slot.subject_id)
            out.missing.append({"subject_id": str(slot.subject_id), "subject": subj.name if subj else None,
                                "position": slot.position, "reason": reason, "detail": detail})

        if slot.paper_version_id:
            version = db.get(PaperVersion, slot.paper_version_id)
            paper = db.get(Paper, version.paper_id) if version else None
            if not version or version.status not in USABLE_FOR_EXPLICIT:
                miss("VERSION_NOT_APPROVED", "The paper version named by the plan is not approved.")
                continue
        else:
            paper = db.get(Paper, slot.paper_id)
            version = db.scalar(select(PaperVersion).where(PaperVersion.paper_id == slot.paper_id, PaperVersion.status == "PUBLISHED")
                                .order_by(PaperVersion.version_no.desc()).limit(1)) if paper else None
            if not version:
                miss("NO_PUBLISHED_VERSION", "The paper has no published version yet.")
                continue
        if paper.subject_id != slot.subject_id:
            miss("SUBJECT_MISMATCH", "The paper belongs to a different subject than the slot.")
            continue
        if not authorized_source(db, plan.provider_id, paper):
            miss("NOT_AUTHORIZED", "The paper is not shared with this provider.")
            continue
        if not may_use_platform_paper(db, plan.global_test_id, paper, version.paper_version_id):
            miss("RESERVED_FOR_GLOBAL_TEST", "This platform paper belongs to a Global Daily Test; schedule that test instead.")
            continue
        out.resolved.append(Resolved(slot, version, paper))
    return out


def eligible_students(db: Session, plan: ExamPlan) -> list[tuple[str, str, UUID | None]]:
    """The plan's audience resolved from CURRENT data (see `resolve_audience`)."""
    batch_ids = [b for (b,) in db.execute(select(ExamPlanBatch.batch_id).where(ExamPlanBatch.plan_id == plan.plan_id))]
    student_ids = [sid for (sid,) in db.execute(select(ExamPlanStudent.student_id).where(ExamPlanStudent.plan_id == plan.plan_id))]
    return resolve_audience(db, plan.provider_id, plan.branch_id, plan.audience_type, batch_ids, student_ids)


def resolve_audience(db: Session, provider_id: UUID, branch_id: UUID, audience_type: str, batch_ids: list[UUID],
                     student_ids: list[str]) -> list[tuple[str, str, UUID | None]]:
    """(student_id, enrollment_id, batch_id), one row per student. Used by generation and by the wizard's audience preview.

    BATCHES / BRANCH / INSTITUTE: an ACTIVE enrollment at the provider (in the plan's branch for BATCHES and BRANCH) and an
    active membership in an ACTIVE batch of that enrollment's branch (the listed batches / any batch of the branch / any batch
    of any active branch). STUDENTS: the listed students with an ACTIVE enrollment at the provider (the plan's branch is
    preferred when a student is enrolled in several branches); a listed student without one is not assigned."""
    if audience_type == "STUDENTS":
        if not student_ids:
            return []
        rows = db.execute(
            select(Enrollment.student_id, Enrollment.enrollment_id, Enrollment.branch_id)
            .where(Enrollment.provider_id == provider_id, Enrollment.status == "ACTIVE", Enrollment.student_id.in_(student_ids))
            .order_by(Enrollment.student_id, Enrollment.enrolled_at)).all()
        best: dict[str, tuple[str, str, None]] = {}
        for sid, eid, bid in rows:
            if sid not in best or bid == branch_id:
                best[sid] = (sid, eid, None)
        return [best[sid] for sid in sorted(best)]

    stmt = (select(Enrollment.student_id, Enrollment.enrollment_id, EnrollmentBatch.batch_id)
            .join(EnrollmentBatch, EnrollmentBatch.enrollment_id == Enrollment.enrollment_id)
            .join(Batch, Batch.batch_id == EnrollmentBatch.batch_id)
            .where(Enrollment.provider_id == provider_id, Enrollment.status == "ACTIVE", Batch.status == "ACTIVE",
                   Batch.branch_id == Enrollment.branch_id))
    if audience_type == "BATCHES":
        if not batch_ids:
            return []
        stmt = stmt.where(Enrollment.branch_id == branch_id, EnrollmentBatch.batch_id.in_(batch_ids))
    elif audience_type == "BRANCH":
        stmt = stmt.where(Enrollment.branch_id == branch_id)
    else:  # INSTITUTE
        active_branches = select(Branch.branch_id).where(Branch.provider_id == provider_id, Branch.status == "ACTIVE",
                                                         Branch.deleted_at.is_(None))
        stmt = stmt.where(Enrollment.branch_id.in_(active_branches))
    rows = db.execute(stmt.order_by(Enrollment.student_id, EnrollmentBatch.batch_id)).all()
    seen, out = set(), []
    for sid, eid, bid in rows:
        if sid not in seen:
            seen.add(sid)
            out.append((sid, eid, bid))
    return out


def already_holding_global_test(db: Session, plan: ExamPlan, start: datetime, student_ids: list[str]) -> set[str]:
    """Students who already have this Global Daily Test for the same start time through ANOTHER plan of the same provider."""
    if not plan.global_test_id or not student_ids:
        return set()
    rows = db.execute(
        select(StudentExamAssignment.student_id)
        .join(ExamVersion, ExamVersion.exam_version_id == StudentExamAssignment.exam_version_id)
        .join(Exam, Exam.exam_id == StudentExamAssignment.exam_id)
        .join(ExamPlan, ExamPlan.plan_id == Exam.plan_id)
        .where(ExamPlan.global_test_id == plan.global_test_id, ExamPlan.provider_id == plan.provider_id, ExamPlan.plan_id != plan.plan_id,
               Exam.status != "CANCELLED", ExamVersion.start_at == start, StudentExamAssignment.student_id.in_(student_ids))).all()
    return {sid for (sid,) in rows}


def _event(db: Session, job: ExamGenerationJob, kind: str, details: dict | None = None) -> None:
    db.add(ExamGenerationEvent(job_id=job.job_id, event_type=kind, details=details))


def _notify_failure(db: Session, plan: ExamPlan, job: ExamGenerationJob, message: str) -> None:
    staff = responsible_staff(db, plan.provider_id, plan.branch_id, institute=plan.audience_type == "INSTITUTE")
    notify(db, staff, kind="EXAM_GENERATION_FAILED", title=f"Exam generation failed: {plan.name}",
           body=f"{message}\n\nRun: {job.run_key}", dedupe=f"genfail:{job.job_id}:{job.attempts}", provider_id=plan.provider_id)


def canonical_run_key(start: datetime) -> str:
    """The one identity of a generation run: the plan (unique constraint scope) plus its window start, normalised to UTC seconds."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return "window:" + start.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def prior_run(db: Session, plan_id: UUID, start: datetime) -> ExamGenerationJob | None:
    """A successful run of this plan for the same window start, whatever key it was recorded under (older manual/scheduled keys)."""
    return db.scalar(
        select(ExamGenerationJob).join(ExamVersion, ExamVersion.generation_job_id == ExamGenerationJob.job_id)
        .where(ExamGenerationJob.plan_id == plan_id, ExamGenerationJob.status.in_(("SUCCEEDED", "PARTIAL")), ExamVersion.start_at == start)
        .order_by(ExamGenerationJob.started_at).limit(1))


class PlanNotGeneratable(ValueError):
    """The plan cannot generate (missing, not ACTIVE, or its Global Daily Test is not published)."""


def generate(db: Session, plan_id: UUID, run_key: str | None = None, *, actor: UUID | None = None,
             start_at: datetime | None = None, end_at: datetime | None = None, now: datetime | None = None) -> ExamGenerationJob:
    """Idempotent per (plan, window start). Returns the job (check job.status / job.exam_id). `run_key` is accepted for
    compatibility but the canonical key is always used, so every caller converges on one exam per window."""
    now = now or utcnow()
    plan = db.get(ExamPlan, plan_id)
    if not plan or plan.status != "ACTIVE":
        raise PlanNotGeneratable("Exam plan not found or inactive.")
    require_published_global_test(db, plan)
    window_start = start_at or plan.start_at
    run_key = canonical_run_key(window_start)
    earlier = prior_run(db, plan_id, window_start)
    if earlier and earlier.run_key != run_key:
        return earlier   # generated before under an older key: same exam, nothing new

    job = db.scalar(select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan_id, ExamGenerationJob.run_key == run_key))
    if job:
        if job.status in {"SUCCEEDED", "PARTIAL"}:
            return job  # replay: same result, nothing duplicated
        if job.status == "RUNNING" and now - job.started_at < STALE_RUNNING:
            return job  # another worker is on it
        job.attempts += 1
        job.status, job.error, job.started_at, job.finished_at = "RUNNING", None, now, None
        db.commit()
    else:
        job = ExamGenerationJob(plan_id=plan_id, run_key=run_key, requested_by=actor, started_at=now)
        db.add(job)
        try:
            db.commit()
        except IntegrityError:  # lost the race to a concurrent request
            db.rollback()
            return db.scalar(select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan_id, ExamGenerationJob.run_key == run_key))

    try:
        res = resolve_slots(db, plan)
        _event(db, job, "RESOLVED", {"resolved": len(res.resolved), "missing": res.missing})
        if not res.resolved:
            job.status, job.error, job.finished_at = "FAILED", "No approved papers are available for any subject.", utcnow()
            _event(db, job, "FAILED", {"reason": job.error})
            db.commit()
            _notify_failure(db, plan, job, job.error)
            for m in res.missing:
                _notify_missing(db, plan, job, m)
            return job

        require_published_global_test(db, plan, lock=True)   # withdrawal racing this generation waits for the row lock
        students = eligible_students(db, plan)
        start, end = start_at or plan.start_at, end_at or plan.end_at
        duplicates = already_holding_global_test(db, plan, start, [sid for sid, _, _ in students])
        students = [row for row in students if row[0] not in duplicates]
        exam = Exam(plan_id=plan.plan_id, run_key=run_key, provider_id=plan.provider_id, branch_id=plan.branch_id,
                    name=f"{plan.name} ({start.date().isoformat()})", created_by=actor,
                    status="PUBLISHED" if plan.auto_publish else "SCHEDULED")
        db.add(exam)
        db.flush()
        total_q = sum(r.version.question_count for r in res.resolved)
        if plan.marking_mode == "UNIFORM":
            total_m = Decimal(plan.marks_correct) * total_q          # every question carries the exam-level marks
        else:
            total_m = sum((r.version.total_marks for r in res.resolved), Decimal(0))
        ev = ExamVersion(exam_id=exam.exam_id, version_no=1, duration_minutes=plan.duration_minutes, start_at=start, end_at=end,
                         total_questions=total_q, total_marks=total_m, missing_subjects=res.missing or None,
                         settings={**plan_settings(plan), "skipped_duplicates": len(duplicates)},
                         generation_job_id=job.job_id, published_at=utcnow() if plan.auto_publish else None)
        db.add(ev)
        db.flush()
        for pos, r in enumerate(res.resolved, start=1):
            db.add(ExamVersionPaper(exam_version_id=ev.exam_version_id, position=pos, subject_id=r.slot.subject_id,
                                    paper_version_id=r.version.paper_version_id, source_provider_id=r.paper.provider_id,
                                    question_count=r.version.question_count, total_marks=r.version.total_marks))
        for sid, eid, bid in students:
            db.add(StudentExamAssignment(exam_id=exam.exam_id, exam_version_id=ev.exam_version_id, student_id=sid,
                                         enrollment_id=eid, batch_id=bid))
        if plan.auto_publish:
            db.add(ExamOutbox(exam_version_id=ev.exam_version_id, operation="PUBLISH"))
        job.exam_id = exam.exam_id
        job.status = "PARTIAL" if res.missing else "SUCCEEDED"
        job.finished_at = utcnow()
        _event(db, job, "GENERATED", {"exam_id": str(exam.exam_id), "students": len(students), "subjects": len(res.resolved)})
        db.commit()
        audit.record(db, "exam.generate", "SUCCESS", actor=actor, service=None if actor else "exam-scheduler",
                     target_type="exam", target_id=exam.exam_id, provider_id=plan.provider_id, branch_id=plan.branch_id,
                     details={"missing": len(res.missing), "students": len(students)})
        for m in res.missing:
            _notify_missing(db, plan, job, m)
        return job
    except PlanNotGeneratable as exc:   # withdrawn while this run was starting: record it, generate nothing
        db.rollback()
        job = db.get(ExamGenerationJob, job.job_id)
        job.status, job.error, job.finished_at = "FAILED", str(exc), utcnow()
        _event(db, job, "FAILED", {"reason": str(exc)})
        db.commit()
        return job
    except Exception as exc:  # leave a FAILED job so the run is visible and retryable
        db.rollback()
        job = db.get(ExamGenerationJob, job.job_id)
        job.status, job.error, job.finished_at = "FAILED", f"{type(exc).__name__}: {str(exc)[:300]}", utcnow()
        _event(db, job, "FAILED", {"error": job.error})
        db.commit()
        _notify_failure(db, plan, job, "An unexpected error stopped the generation. It will be retried.")
        return job


def _notify_missing(db: Session, plan: ExamPlan, job: ExamGenerationJob, m: dict) -> None:
    subject_id = UUID(m["subject_id"])
    staff = responsible_staff(db, plan.provider_id, plan.branch_id, subject_id, institute=plan.audience_type == "INSTITUTE")
    notify(db, staff, kind="MISSING_PAPER", title=f"Missing paper: {m.get('subject') or 'subject'} for {plan.name}",
           body=f"{m['detail']} ({m['reason']}). The exam was generated without this subject; "
                "once the paper is approved it will be used in the next exam.",
           dedupe=f"missing:{job.job_id}:{m['subject_id']}", provider_id=plan.provider_id)


def run_due_plans(db: Session, now: datetime | None = None) -> list[ExamGenerationJob]:
    """Scheduler entry point (cron / `python -m app.worker`): generate every active plan whose
    generate_at has passed and which has no job for its run key yet."""
    now = now or utcnow()
    jobs = []
    for plan in db.scalars(select(ExamPlan).where(ExamPlan.status == "ACTIVE", ExamPlan.generate_at.is_not(None),
                                                  ExamPlan.generate_at <= now)):
        existing = db.scalar(select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan.plan_id,
                                                             ExamGenerationJob.run_key == canonical_run_key(plan.start_at)))
        if (existing and existing.status in {"SUCCEEDED", "PARTIAL"}) or prior_run(db, plan.plan_id, plan.start_at):
            continue   # already generated for this window (manually or by an earlier scheduler run)
        try:
            jobs.append(generate(db, plan.plan_id, now=now))
        except PlanNotGeneratable:
            continue   # e.g. its Global Daily Test was withdrawn
    return jobs


def require_published_global_test(db: Session, plan: ExamPlan, *, lock: bool = False) -> None:
    """A plan that schedules a Global Daily Test generates only while that test is PUBLISHED. With `lock`, the test row is locked
    (SELECT ... FOR UPDATE) for the rest of the transaction, so a concurrent withdrawal is serialised after this generation."""
    if not plan.global_test_id:
        return
    stmt = select(GlobalTest).where(GlobalTest.test_id == plan.global_test_id)
    if lock:
        stmt = stmt.with_for_update()
    gt = db.scalar(stmt)
    if not gt or gt.status != "PUBLISHED":
        raise PlanNotGeneratable("The Global Daily Test of this plan is not published (withdrawn or never published).")
