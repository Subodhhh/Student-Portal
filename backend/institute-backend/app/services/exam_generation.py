"""Automatic exam generation.

Rules implemented here (all unit-tested):
  * only approved/published paper versions, and only papers the plan's provider owns or has
    been granted through an ACTIVE share;
  * exact version if the plan names one, otherwise the paper's latest PUBLISHED version;
  * a missing/unauthorized subject never blocks the others: the exam is generated from what is
    available and the gaps are recorded and notified;
  * the manifest (exam_version_papers) is immutable, so later approvals, mapping changes or
    new paper versions affect only the NEXT generation, never an existing exam;
  * idempotent per (plan, run_key): a retry returns the same exam, never duplicates exams,
    assignments or notifications.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.database import utcnow
from app.models.exams import (
    Exam, ExamGenerationEvent, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanSubject,
    ExamVersion, ExamVersionPaper, StudentExamAssignment,
)
from app.models.org import Batch, Enrollment, EnrollmentBatch
from app.models.academics import Subject
from app.models.papers import Paper, PaperShare, PaperVersion
from app.services.notify import notify, responsible_staff

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


def authorized_source(db: Session, plan_provider_id: UUID, paper: Paper) -> bool:
    if paper.provider_id == plan_provider_id:
        return True
    return db.scalar(select(PaperShare).where(
        PaperShare.paper_id == paper.paper_id, PaperShare.shared_with_provider_id == plan_provider_id,
        PaperShare.status == "ACTIVE")) is not None


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
        out.resolved.append(Resolved(slot, version, paper))
    return out


def eligible_students(db: Session, plan: ExamPlan) -> list[tuple[str, str, UUID]]:
    """(student_id, enrollment_id, batch_id): ACTIVE enrollment in the plan's branch and an
    active batch membership in one of the plan's batches. One assignment per student."""
    batch_ids = [b for (b,) in db.execute(select(ExamPlanBatch.batch_id).where(ExamPlanBatch.plan_id == plan.plan_id))]
    if not batch_ids:
        return []
    rows = db.execute(
        select(Enrollment.student_id, Enrollment.enrollment_id, EnrollmentBatch.batch_id)
        .join(EnrollmentBatch, EnrollmentBatch.enrollment_id == Enrollment.enrollment_id)
        .join(Batch, Batch.batch_id == EnrollmentBatch.batch_id)
        .where(Enrollment.branch_id == plan.branch_id, Enrollment.provider_id == plan.provider_id,
               Enrollment.status == "ACTIVE", EnrollmentBatch.batch_id.in_(batch_ids), Batch.status == "ACTIVE")
        .order_by(Enrollment.student_id, EnrollmentBatch.batch_id)
    ).all()
    seen, out = set(), []
    for sid, eid, bid in rows:
        if sid not in seen:
            seen.add(sid)
            out.append((sid, eid, bid))
    return out


def _event(db: Session, job: ExamGenerationJob, kind: str, details: dict | None = None) -> None:
    db.add(ExamGenerationEvent(job_id=job.job_id, event_type=kind, details=details))


def _notify_failure(db: Session, plan: ExamPlan, job: ExamGenerationJob, message: str) -> None:
    staff = responsible_staff(db, plan.provider_id, plan.branch_id)
    notify(db, staff, kind="EXAM_GENERATION_FAILED", title=f"Exam generation failed: {plan.name}",
           body=f"{message}\n\nRun: {job.run_key}", dedupe=f"genfail:{job.job_id}:{job.attempts}", provider_id=plan.provider_id)


def generate(db: Session, plan_id: UUID, run_key: str, *, actor: UUID | None = None,
             start_at: datetime | None = None, end_at: datetime | None = None, now: datetime | None = None) -> ExamGenerationJob:
    """Idempotent. Returns the job (check job.status / job.exam_id)."""
    now = now or utcnow()
    plan = db.get(ExamPlan, plan_id)
    if not plan or plan.status != "ACTIVE":
        raise ValueError("Exam plan not found or inactive.")

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

        students = eligible_students(db, plan)
        start, end = start_at or plan.start_at, end_at or plan.end_at
        exam = Exam(plan_id=plan.plan_id, run_key=run_key, provider_id=plan.provider_id, branch_id=plan.branch_id,
                    name=f"{plan.name} ({start.date().isoformat()})", created_by=actor,
                    status="PUBLISHED" if plan.auto_publish else "SCHEDULED")
        db.add(exam)
        db.flush()
        total_q = sum(r.version.question_count for r in res.resolved)
        total_m = sum((r.version.total_marks for r in res.resolved), Decimal(0))
        ev = ExamVersion(exam_id=exam.exam_id, version_no=1, duration_minutes=plan.duration_minutes, start_at=start, end_at=end,
                         total_questions=total_q, total_marks=total_m, missing_subjects=res.missing or None,
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
    staff = responsible_staff(db, plan.provider_id, plan.branch_id, subject_id)
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
        run_key = f"scheduled:{plan.start_at.isoformat()}"
        existing = db.scalar(select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan.plan_id, ExamGenerationJob.run_key == run_key))
        if existing and existing.status in {"SUCCEEDED", "PARTIAL"}:
            continue
        jobs.append(generate(db, plan.plan_id, run_key, now=now))
    return jobs
