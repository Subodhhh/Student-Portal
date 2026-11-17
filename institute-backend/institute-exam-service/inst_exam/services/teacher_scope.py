"""Which tests (and which of their students) a TEACHER may see.

A teacher sees a test when at least one student TARGETED by it is in the teacher's teaching scope for at least one of the test's
subjects: an active teaching assignment (provider, branch, subject, optionally one batch) whose subject is one of the test's subjects,
in the branch of the enrollment the student was targeted through and, for a batch-level assignment, with the student in that batch.
One matching subject is enough (a Physics teacher sees a Physics + Chemistry test of their students); teaching the subject in another
batch or branch is not. Subject-wise enrollment: the student must also take the teacher's subject in a matching batch (a membership
without a subject selection takes every subject; a student in no batch keeps the branch-wide rule).

The targeted students are the exam's assignments once the exam exists (exactly who sat it), otherwise the plan's audience resolved
from current enrollments by the generator's own rule (exam_generation.eligible_students). Only the matching students are ever shown to
the teacher; other students of the same test stay hidden. Nothing here grants any permission beyond reading.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.core.security import Principal
from institute_common.models.exams import Exam, ExamPlan, ExamPlanSubject, StudentExamAssignment
from institute_common.models.org import Enrollment, EnrollmentBatch
from inst_exam.services import exam_generation as gen

Target = tuple[str, str, UUID | None]   # (student_id, enrollment_id, batch_id the student was targeted through, if any)


def plan_subjects(db: Session, plan_id: UUID) -> set[UUID]:
    return set(db.scalars(select(ExamPlanSubject.subject_id).where(ExamPlanSubject.plan_id == plan_id)))


def exam_targets(db: Session, exam_id: UUID) -> list[Target]:
    return [tuple(r) for r in db.execute(select(StudentExamAssignment.student_id, StudentExamAssignment.enrollment_id,
                                                StudentExamAssignment.batch_id).where(StudentExamAssignment.exam_id == exam_id))]


def plan_targets(db: Session, plan: ExamPlan) -> list[Target]:
    exam = db.scalar(select(Exam).where(Exam.plan_id == plan.plan_id).order_by(Exam.created_at.desc()).limit(1))
    return exam_targets(db, exam.exam_id) if exam else gen.eligible_students(db, plan)


def matching_students(db: Session, p: Principal, provider_id: UUID, subject_ids: set[UUID], targets: list[Target],
                      branch_id: UUID | None = None, memberships=None) -> set[str]:
    """The targeted students inside the caller's teaching scope for one of `subject_ids` (only that branch's teaching, if given).
    `memberships`: a callable giving _memberships of (at least) the targets' enrollments, already read for several calls of a request."""
    taught = _taught(p, provider_id, subject_ids, branch_id)
    if not taught or not targets:
        return set()
    return _match(taught, targets, *(memberships() if memberships else _memberships(db, {eid for _sid, eid, _bid in targets})))


def _taught(p: Principal, provider_id: UUID, subject_ids: set[UUID], branch_id: UUID | None = None) -> list:
    return [t for t in p.teaching if t.provider_id == provider_id and t.subject_id in subject_ids and branch_id in (None, t.branch_id)]


def _memberships(db: Session, enrollment_ids: set[str]) -> tuple[dict, dict, dict]:
    """Each enrollment's branch, its batches and its subject choices (keyed by enrollment: safe to share between plans)."""
    branch_of = dict(db.execute(select(Enrollment.enrollment_id, Enrollment.branch_id).where(Enrollment.enrollment_id.in_(enrollment_ids))).all())
    batches: dict[str, set[UUID]] = {}
    for eid, bid in db.execute(select(EnrollmentBatch.enrollment_id, EnrollmentBatch.batch_id).where(EnrollmentBatch.enrollment_id.in_(enrollment_ids))):
        batches.setdefault(eid, set()).add(bid)
    return branch_of, batches, gen.subjects_taken(db, enrollment_ids)


def _match(taught: list, targets: list[Target], branch_of: dict, batches: dict, chosen: dict) -> set[str]:
    def takes(eid: str, batch: UUID, subject: UUID) -> bool:
        return subject in chosen.get((eid, batch), {subject})

    def in_scope(t, eid: str, in_batches: set[UUID]) -> bool:
        if t.batch_id is not None:
            return t.batch_id in in_batches and takes(eid, t.batch_id, t.subject_id)
        return not in_batches or any(takes(eid, b, t.subject_id) for b in in_batches)

    out = set()
    for sid, eid, bid in targets:
        branch = branch_of.get(eid)
        in_batches = batches.get(eid, set()) | ({bid} if bid else set())
        if any(t.branch_id == branch and in_scope(t, eid, in_batches) for t in taught):
            out.add(sid)
    return out


def plan_students(db: Session, p: Principal, plan: ExamPlan) -> set[str]:
    return matching_students(db, p, plan.provider_id, plan_subjects(db, plan.plan_id), plan_targets(db, plan))


def plans_students(db: Session, p: Principal, plans: list[ExamPlan]) -> dict[UUID, set[str]]:
    """plan_students for many plans in a fixed number of statements (the Scheduled Tests list checks every plan of a teacher's providers):
    the plans' subjects, their latest exams, those exams' assignments and the targeted enrollments' memberships, each read once for all
    plans. A plan whose subjects the teacher does not teach has no student in scope, so its targets are never read (as matching_students
    returns nothing for it). A plan without an exam resolves its current audience as gen.eligible_students does, from bulk-read inputs."""
    out: dict[UUID, set[str]] = {plan.plan_id: set() for plan in plans}
    if not plans:
        return out
    subjects: dict[UUID, set[UUID]] = {}
    for pid, sid in db.execute(select(ExamPlanSubject.plan_id, ExamPlanSubject.subject_id).where(ExamPlanSubject.plan_id.in_(list(out)))):
        subjects.setdefault(pid, set()).add(sid)
    taught = {plan.plan_id: _taught(p, plan.provider_id, subjects.get(plan.plan_id, set())) for plan in plans}
    wanted = [plan for plan in plans if taught[plan.plan_id]]
    if not wanted:
        return out
    latest: dict[UUID, Exam] = {}
    for e in db.scalars(select(Exam).where(Exam.plan_id.in_([plan.plan_id for plan in wanted])).order_by(Exam.created_at)):
        latest[e.plan_id] = e                                              # the last one: plan_targets' newest exam
    targets: dict[UUID, list[Target]] = {plan.plan_id: [] for plan in wanted}
    by_exam = {e.exam_id: pid for pid, e in latest.items()}
    for sid, eid, bid, exam_id in db.execute(select(StudentExamAssignment.student_id, StudentExamAssignment.enrollment_id,
                                                    StudentExamAssignment.batch_id, StudentExamAssignment.exam_id)
                                             .where(StudentExamAssignment.exam_id.in_(list(by_exam) or [None]))):
        targets[by_exam[exam_id]].append((sid, eid, bid))
    no_exam = [plan for plan in wanted if plan.plan_id not in latest]
    if no_exam:
        # The current audience of each plan without an exam, by the same rules (gen.eligible_students): its plan details for all such plans
        # in one statement, and the batch-based rules applied to one read-only snapshot of each provider's memberships (the snapshot the
        # Global date page uses), opted in only for these reads and switched back off afterwards.
        inputs = gen.eligible_inputs(db, [plan.plan_id for plan in no_exam])
        before = {k: db.info[k] for k in ("audience_snapshot", "audience_snapshot_providers") if k in db.info}
        db.info["audience_snapshot"] = True
        db.info["audience_snapshot_providers"] = {plan.provider_id for plan in no_exam}
        try:
            for plan in no_exam:
                targets[plan.plan_id] = gen.eligible_students(db, plan, inputs=inputs[plan.plan_id])
        finally:
            for k in ("audience_snapshot", "audience_snapshot_providers"):
                db.info.pop(k, None)
            db.info.update(before)
    enrollment_ids = {eid for rows in targets.values() for _sid, eid, _bid in rows}
    if not enrollment_ids:
        return out
    memberships = _memberships(db, enrollment_ids)
    for plan in wanted:
        if targets[plan.plan_id]:
            out[plan.plan_id] = _match(taught[plan.plan_id], targets[plan.plan_id], *memberships)
    return out


def exam_students(db: Session, p: Principal, exam: Exam, branch_id: UUID | None = None) -> set[str]:
    hit = db.info.get("teacher_exam_inputs", {}).get((p.staff_id, exam.exam_id))
    if hit is not None:                                   # read by prefetch_exam_students for this request: the same rule, no statement
        subject_ids, targets = hit
        taught = _taught(p, exam.provider_id, subject_ids, branch_id)
        if not taught or not targets:
            return set()
        return _match(taught, targets, *db.info["teacher_exam_memberships"])
    return matching_students(db, p, exam.provider_id, plan_subjects(db, exam.plan_id), exam_targets(db, exam.exam_id), branch_id)


def prefetch_exam_students(db: Session, p: Principal, exams) -> None:
    """exam_students' reads for a page of exams in a fixed number of statements (lists ask exam_access, and so exam_students, per exam):
    the exams' plan subjects, their assignments and those enrollments' memberships, kept on this request's session only (db.info) and
    read by exam_students instead of its per-exam queries. Nothing is decided here: exam_students still applies matching_students' rule.
    An exam whose subjects this teacher does not teach needs no targets (no student can be in scope); they are not read for it."""
    cache = db.info.setdefault("teacher_exam_inputs", {})
    wanted = [e for e in exams if (p.staff_id, e.exam_id) not in cache]
    if not wanted:
        return
    subjects: dict[UUID, set[UUID]] = {}
    plan_ids = {e.plan_id for e in wanted if e.plan_id}
    if plan_ids:
        for pid, sid in db.execute(select(ExamPlanSubject.plan_id, ExamPlanSubject.subject_id).where(ExamPlanSubject.plan_id.in_(plan_ids))):
            subjects.setdefault(pid, set()).add(sid)
    taught = [e for e in wanted if _taught(p, e.provider_id, subjects.get(e.plan_id, set()))]
    targets: dict[UUID, list[Target]] = {e.exam_id: [] for e in taught}
    if taught:
        for sid, eid, bid, exam_id in db.execute(select(StudentExamAssignment.student_id, StudentExamAssignment.enrollment_id,
                                                        StudentExamAssignment.batch_id, StudentExamAssignment.exam_id)
                                                 .where(StudentExamAssignment.exam_id.in_(list(targets)))):
            targets[exam_id].append((sid, eid, bid))
    enrollment_ids = {eid for rows in targets.values() for _sid, eid, _bid in rows}
    if enrollment_ids:
        branch_of, batches, chosen = _memberships(db, enrollment_ids)
        held = db.info.setdefault("teacher_exam_memberships", ({}, {}, {}))
        held[0].update(branch_of)
        held[1].update(batches)
        held[2].update(chosen)
    else:
        db.info.setdefault("teacher_exam_memberships", ({}, {}, {}))
    for e in wanted:                                      # an exam not taught: its subjects alone already decide "no student"
        cache[(p.staff_id, e.exam_id)] = (subjects.get(e.plan_id, set()), targets.get(e.exam_id, []))
