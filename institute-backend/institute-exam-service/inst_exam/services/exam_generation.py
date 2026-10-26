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
    batch memberships at generation time; each student is assigned once, and a student already holding the same Global
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
    ExamPlanBranch,
    Exam, ExamGenerationEvent, ExamGenerationJob, ExamOutbox, ExamPlan, ExamPlanBatch, ExamPlanStudent, ExamPlanSubject,
    ExamVersion, ExamVersionPaper, GlobalPaperSubmission, GlobalTest, GlobalTestSection, GlobalTestStudent, GlobalTestStudentSubject,
    StudentExamAssignment,
)
from institute_common.models.org import Batch, Branch, Enrollment, EnrollmentBatch
from institute_common.core.class_levels import class_target
from institute_common.models.academics import BatchSubject, EnrollmentAcademicProfile, EnrollmentBatchSubject, Subject
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
    """A platform paper that belongs to a Global Test (any of its versions is a test section) is scheduled only through that
    test's own plan, with exactly the section's version; an ordinary plan cannot reuse it with other marking or duration.
    Platform papers that are in no Global Test are freely usable."""
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
        if slot.source_submission_id:      # unified Global Test plan: authorized by its provider's submission, never by the plan's provider
            sub = db.get(GlobalPaperSubmission, slot.source_submission_id)
            if not (sub and plan.global_occurrence_id and sub.occurrence_id == plan.global_occurrence_id and sub.status == "ACTIVE"
                    and sub.subject_id == slot.subject_id and sub.paper_version_id == version.paper_version_id
                    and paper.provider_id == sub.provider_id):
                miss("NOT_AUTHORIZED", "The paper is not an active submission of its provider for this Global Test date.")
                continue
        elif not authorized_source(db, plan.provider_id, paper):
            miss("NOT_AUTHORIZED", "The paper is not shared with this provider.")
            continue
        if not may_use_platform_paper(db, plan.global_test_id, paper, version.paper_version_id):
            miss("RESERVED_FOR_GLOBAL_TEST", "This platform paper belongs to a Global Test; schedule that test instead.")
            continue
        out.resolved.append(Resolved(slot, version, paper))
    return out


UNMATCHABLE = "!unmatchable"   # a class code no enrollment has: a plan whose target cannot be matched reaches nobody through batches


def eligible_students(db: Session, plan: ExamPlan, report: dict | None = None) -> list[tuple[str, str, UUID | None]]:
    """The plan's audience resolved from CURRENT data (see `resolve_audience`), including its Class (plan.target_class)."""
    if unified_plan(db, plan.plan_id):
        return unified_students(db, plan)
    class_level, problem = class_target(plan.target_class)
    if problem:                 # fail-safe for every reader (generation itself refuses such a plan before calling this)
        class_level = UNMATCHABLE
        if report is not None:
            report["class_problem"] = problem
    batch_ids = [b for (b,) in db.execute(select(ExamPlanBatch.batch_id).where(ExamPlanBatch.plan_id == plan.plan_id))]
    student_ids = [sid for (sid,) in db.execute(select(ExamPlanStudent.student_id).where(ExamPlanStudent.plan_id == plan.plan_id))]
    branch_ids = [b for (b,) in db.execute(select(ExamPlanBranch.branch_id).where(ExamPlanBranch.plan_id == plan.plan_id))]
    subject_ids = list(db.scalars(select(ExamPlanSubject.subject_id).where(ExamPlanSubject.plan_id == plan.plan_id)))
    return resolve_audience(db, plan.provider_id, plan.branch_id, plan.audience_type, batch_ids, student_ids, branch_ids=branch_ids,
                            subject_ids=subject_ids, class_level=class_level, report=report)


def unified_plan(db: Session, plan_id: UUID) -> bool:
    """A unified Global Test plan: it has per-student rows of its date (global_test_students), or (dates generated before those rows
    existed) its slots come from provider submissions."""
    return bool(db.scalar(select(GlobalTestStudent.student_id).where(GlobalTestStudent.plan_id == plan_id).limit(1))) \
        or bool(unified_sources(db, plan_id))


def unified_layout(db: Session, plan: ExamPlan) -> list[dict]:
    """The configured subjects of a unified plan in order, each with the paper or the reason there is none (all students of one plan
    share it). Empty for plans of dates generated before per-student rows existed."""
    first = db.scalar(select(GlobalTestStudent.student_id).where(GlobalTestStudent.plan_id == plan.plan_id).order_by(GlobalTestStudent.student_id).limit(1))
    if first is None:
        return []
    rows = db.execute(select(GlobalTestStudentSubject, Subject.name).join(Subject, Subject.subject_id == GlobalTestStudentSubject.subject_id)
                      .where(GlobalTestStudentSubject.occurrence_id == plan.global_occurrence_id, GlobalTestStudentSubject.student_id == first)
                      .order_by(GlobalTestStudentSubject.position)).all()
    return [{"position": c.position, "subject_id": str(c.subject_id), "subject": name, "status": c.status,
             "provider_id": str(c.provider_id) if c.provider_id else None} for c, name in rows]


def unified_sources(db: Session, plan_id: UUID) -> list[GlobalPaperSubmission]:
    """The submissions a unified Global Test plan's subjects come from, in slot order (empty for every other plan)."""
    return list(db.scalars(select(GlobalPaperSubmission).join(ExamPlanSubject, ExamPlanSubject.source_submission_id == GlobalPaperSubmission.submission_id)
                           .where(ExamPlanSubject.plan_id == plan_id).order_by(ExamPlanSubject.position)))


def unified_students(db: Session, plan: ExamPlan) -> list[tuple[str, str, UUID | None]]:
    """A unified plan's audience: its listed students (each resolved by every contributing submission at the deadline) who are still
    actively enrolled with EVERY provider whose paper they receive. The plan's own provider/branch play no part. The assignment
    records the enrollment of the first subject's provider (preferring that submission's branch) only as a reference."""
    sources = unified_sources(db, plan.plan_id)
    listed = [sid for (sid,) in db.execute(select(ExamPlanStudent.student_id).where(ExamPlanStudent.plan_id == plan.plan_id)
                                           .order_by(ExamPlanStudent.student_id))]
    if not sources:
        # a group without any usable paper: still enrolled (reference enrollment, else any ACTIVE one) -> a card that cannot be started
        ref = dict(db.execute(select(GlobalTestStudent.student_id, GlobalTestStudent.enrollment_id).where(GlobalTestStudent.plan_id == plan.plan_id)).all())
        current: dict[str, list[str]] = {}
        for sid, eid in db.execute(select(Enrollment.student_id, Enrollment.enrollment_id).where(Enrollment.status == "ACTIVE", Enrollment.student_id.in_(listed or [""]))
                                   .order_by(Enrollment.enrolled_at, Enrollment.enrollment_id)):
            current.setdefault(sid, []).append(eid)
        return [(sid, ref[sid] if ref.get(sid) in current[sid] else current[sid][0], None) for sid in listed if sid in current]
    active: dict[UUID, dict[str, list[tuple[str, UUID]]]] = {}
    for prov in {s.provider_id for s in sources}:
        rows: dict[str, list[tuple[str, UUID]]] = {}
        for sid, eid, br in db.execute(select(Enrollment.student_id, Enrollment.enrollment_id, Enrollment.branch_id)
                                       .where(Enrollment.provider_id == prov, Enrollment.status == "ACTIVE", Enrollment.student_id.in_(listed or [""]))
                                       .order_by(Enrollment.enrollment_id)):
            rows.setdefault(sid, []).append((eid, br))
        active[prov] = rows
    first = sources[0] if sources else None
    out = []
    for sid in listed:
        if first and all(sid in active[prov] for prov in active):
            mine = active[first.provider_id][sid]
            eid = next((e for e, br in mine if br == first.branch_id), mine[0][0])
            out.append((sid, eid, None))
    return out


def subjects_taken(db: Session, enrollment_ids) -> dict[tuple[str, UUID], set[UUID]]:
    """(enrollment_id, batch_id) -> the subjects chosen in that batch membership. A membership missing here has no selection and
    takes every subject its batch offers."""
    out: dict[tuple[str, UUID], set[UUID]] = {}
    for eid, bid, sid in db.execute(
            select(EnrollmentBatch.enrollment_id, EnrollmentBatch.batch_id, EnrollmentBatchSubject.subject_id)
            .join(EnrollmentBatchSubject, EnrollmentBatchSubject.enrollment_batch_id == EnrollmentBatch.enrollment_batch_id)
            .where(EnrollmentBatch.enrollment_id.in_(list(enrollment_ids) or [None]))):
        out.setdefault((eid, bid), set()).add(sid)
    return out


def batch_subjects(db: Session, batch_ids) -> dict[UUID, set[UUID]]:
    """batch_id -> the subjects that batch offers (what a membership without a subject selection takes, for Global Tests)."""
    out: dict[UUID, set[UUID]] = {}
    for bid, sid in db.execute(select(BatchSubject.batch_id, BatchSubject.subject_id).where(BatchSubject.batch_id.in_(list(batch_ids) or [None]))):
        out.setdefault(bid, set()).add(sid)
    return out


def resolve_audience(db: Session, provider_id: UUID, branch_id: UUID, audience_type: str, batch_ids: list[UUID],
                     student_ids: list[str], *, branch_ids: list[UUID] = (), subject_ids=(), class_level: str | None = None,
                     report: dict | None = None, unselected_takes_batch_subjects: bool = False) -> list[tuple[str, str, UUID | None]]:
    """(student_id, enrollment_id, batch_id), one row per student. Used by generation and by the wizard's audience preview.

    BATCHES / BRANCH / INSTITUTE: an ACTIVE enrollment at the provider (in the plan's branch for BATCHES and BRANCH) and an
    active membership in an ACTIVE batch of that enrollment's branch (the listed batches / any batch of the branch / any batch
    of any active branch). STUDENTS: the listed students with an ACTIVE enrollment at the provider (the plan's branch is
    preferred when a student is enrolled in several branches); a listed student without one is not assigned.
    TARGETED: the union of whole branches (`branch_ids`, BRANCH rule), batches of any branch of the provider (BATCHES rule in each
    batch's own branch) and listed students (STUDENTS rule); a student reached several ways is assigned once.
    Subject-wise enrollment: with `subject_ids` (the exam's subjects), a batch-based rule counts a membership only if the student takes
    ALL of them in that batch (a membership without a subject selection takes every subject; with
    `unselected_takes_batch_subjects`, used for Global Tests, only the subjects its batch offers). Listed students are a staff override
    and are never filtered by subject.
    Class: with `class_level` (a code from core/class_levels.class_target), batch-based rules also require the ENROLLMENT's academic
    profile to have exactly that Class; enrollments without a profile are excluded. `report` (if given) counts the students excluded
    for each reason (excluded_no_class / excluded_other_class). Listed students are never filtered by Class either."""
    if audience_type == "TARGETED":
        rows: list[tuple[str, str, UUID | None]] = []
        for b in dict.fromkeys(branch_ids):
            rows += resolve_audience(db, provider_id, b, "BRANCH", [], [], subject_ids=subject_ids, class_level=class_level, report=report,
                                     unselected_takes_batch_subjects=unselected_takes_batch_subjects)
        by_branch: dict[UUID, list[UUID]] = {}
        for bid, br in db.execute(select(Batch.batch_id, Batch.branch_id).where(Batch.batch_id.in_(list(batch_ids) or [None]))):
            by_branch.setdefault(br, []).append(bid)
        for br, bids in by_branch.items():
            rows += resolve_audience(db, provider_id, br, "BATCHES", bids, [], subject_ids=subject_ids, class_level=class_level, report=report,
                                     unselected_takes_batch_subjects=unselected_takes_batch_subjects)
        rows += resolve_audience(db, provider_id, branch_id, "STUDENTS", [], list(student_ids))
        seen: set[str] = set()
        unique = []
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                unique.append(row)
        return sorted(unique)
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
    if subject_ids:
        needed, chosen = set(subject_ids), subjects_taken(db, {eid for _sid, eid, _bid in rows})
        if unselected_takes_batch_subjects:
            offered = batch_subjects(db, {bid for _sid, _eid, bid in rows})
            rows = [r for r in rows if needed <= chosen.get((r[1], r[2]), offered.get(r[2], set()))]
        else:
            rows = [r for r in rows if needed <= chosen.get((r[1], r[2]), needed)]
    if class_level:
        classes = dict(db.execute(select(EnrollmentAcademicProfile.enrollment_id, EnrollmentAcademicProfile.class_level)
                                  .where(EnrollmentAcademicProfile.enrollment_id.in_({eid for _sid, eid, _bid in rows} or {None}))).all())
        if report is not None:
            for key, cond in (("excluded_no_class", lambda c: c is None), ("excluded_other_class", lambda c: c is not None and c != class_level)):
                report.setdefault(key, set()).update(sid for sid, eid, _bid in rows if cond(classes.get(eid)))
        rows = [r for r in rows if classes.get(r[1]) == class_level]
    seen, out = set(), []
    for sid, eid, bid in rows:
        if sid not in seen:
            seen.add(sid)
            out.append((sid, eid, bid))
    return out


def already_holding_global_test(db: Session, plan: ExamPlan, start: datetime, student_ids: list[str]) -> set[str]:
    """Students who already have this Global Test for the same start time through ANOTHER plan of the same provider; for a unified plan,
    through ANY other plan of the same occurrence (one exam per student per occurrence, whichever provider)."""
    if not plan.global_test_id or not student_ids:
        return set()
    if plan.global_occurrence_id and unified_plan(db, plan.plan_id):
        rows = db.execute(
            select(StudentExamAssignment.student_id).join(Exam, Exam.exam_id == StudentExamAssignment.exam_id)
            .join(ExamPlan, ExamPlan.plan_id == Exam.plan_id)
            .where(ExamPlan.global_occurrence_id == plan.global_occurrence_id, ExamPlan.plan_id != plan.plan_id, Exam.status != "CANCELLED",
                   StudentExamAssignment.student_id.in_(student_ids))).all()
        return {sid for (sid,) in rows}
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
    if unified_plan(db, plan.plan_id):
        return          # a unified plan belongs to the platform: the failure is on the occurrence (generation_error), not the anchor's inbox
    staff = responsible_staff(db, plan.provider_id, plan.branch_id, institute=plan.audience_type in ("INSTITUTE", "TARGETED"))
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
    """The plan cannot generate (missing, not ACTIVE, or its Global Test is not published)."""


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
        layout = unified_layout(db, plan) if plan.global_occurrence_id else []
        card_only = bool(layout) and not res.missing and not any(c["status"] == "PAPER" for c in layout)
        if not res.resolved and not card_only:
            job.status, job.error, job.finished_at = "FAILED", "No approved papers are available for any subject.", utcnow()
            _event(db, job, "FAILED", {"reason": job.error})
            db.commit()
            _notify_failure(db, plan, job, job.error)
            for m in res.missing:
                _notify_missing(db, plan, job, m)
            return job

        _code, class_problem = class_target(plan.target_class)
        if class_problem:       # never guess a legacy / 'Other' target: refuse instead of silently including or excluding students
            job.status, job.error, job.finished_at = "FAILED", class_problem, utcnow()
            _event(db, job, "FAILED", {"reason": class_problem, "target_class": plan.target_class})
            db.commit()
            _notify_failure(db, plan, job, class_problem)
            return job
        require_published_global_test(db, plan, lock=True)   # withdrawal racing this generation waits for the row lock
        audience: dict = {}
        students = eligible_students(db, plan, audience)
        if plan.target_class:
            _event(db, job, "AUDIENCE", {"target_class": plan.target_class, "students": len(students),
                                         **{k: len(v - {s for s, _, _ in students}) for k, v in audience.items() if isinstance(v, set)}})
        start, end = start_at or plan.start_at, end_at or plan.end_at
        duplicates = already_holding_global_test(db, plan, start, [sid for sid, _, _ in students])
        students = [row for row in students if row[0] not in duplicates]
        exam = Exam(plan_id=plan.plan_id, run_key=run_key, provider_id=plan.provider_id, branch_id=plan.branch_id,
                    name=plan.name if layout else f"{plan.name} ({start.date().isoformat()})", created_by=actor,   # unified: the Global Test's own name
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
                         settings={**plan_settings(plan), "skipped_duplicates": len(duplicates), **({"sections": layout} if layout else {})},
                         generation_job_id=job.job_id, published_at=utcnow() if plan.auto_publish else None)
        db.add(ev)
        db.flush()
        for pos, r in enumerate(res.resolved, start=1):
            db.add(ExamVersionPaper(exam_version_id=ev.exam_version_id, position=pos, subject_id=r.slot.subject_id,
                                    paper_version_id=r.version.paper_version_id, source_provider_id=r.paper.provider_id,
                                    source_submission_id=r.slot.source_submission_id,
                                    question_count=r.version.question_count, total_marks=r.version.total_marks))
        for sid, eid, bid in students:
            db.add(StudentExamAssignment(exam_id=exam.exam_id, exam_version_id=ev.exam_version_id, student_id=sid,
                                         enrollment_id=eid, batch_id=bid))
        if layout:      # in the SAME transaction: the student's one row of the date names this exam (unified from its first moment)
            db.flush()
            for row in db.scalars(select(GlobalTestStudent).where(GlobalTestStudent.plan_id == plan.plan_id,
                                                                  GlobalTestStudent.student_id.in_([sid for sid, _, _ in students] or [""]))):
                row.exam_id = exam.exam_id
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
    provider_id, branch_id, institute = plan.provider_id, plan.branch_id, plan.audience_type in ("INSTITUTE", "TARGETED")
    slot = db.scalar(select(ExamPlanSubject).where(ExamPlanSubject.plan_id == plan.plan_id, ExamPlanSubject.position == m["position"]))
    if slot and slot.source_submission_id:      # unified plan: tell the provider whose paper it was, never the anchor
        sub = db.get(GlobalPaperSubmission, slot.source_submission_id)
        provider_id, branch_id, institute = sub.provider_id, sub.branch_id, True
    staff = responsible_staff(db, provider_id, branch_id, subject_id, institute=institute)
    notify(db, staff, kind="MISSING_PAPER", title=f"Missing paper: {m.get('subject') or 'subject'} for {plan.name}",
           body=f"{m['detail']} ({m['reason']}). The exam was generated without this subject; "
                "once the paper is approved it will be used in the next exam.",
           dedupe=f"missing:{job.job_id}:{m['subject_id']}", provider_id=provider_id)


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
            continue   # e.g. its Global Test was withdrawn
    return jobs


def require_published_global_test(db: Session, plan: ExamPlan, *, lock: bool = False) -> None:
    """A plan that schedules a Global Test generates only while that test is PUBLISHED. With `lock`, the test row is locked
    (SELECT ... FOR UPDATE) for the rest of the transaction, so a concurrent withdrawal is serialised after this generation."""
    if not plan.global_test_id:
        return
    stmt = select(GlobalTest).where(GlobalTest.test_id == plan.global_test_id)
    if lock:
        stmt = stmt.with_for_update()
    gt = db.scalar(stmt)
    if not gt or gt.status != "PUBLISHED":
        raise PlanNotGeneratable("The Global Test of this plan is not published (withdrawn or never published).")
