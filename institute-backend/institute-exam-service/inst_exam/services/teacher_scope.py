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
                      branch_id: UUID | None = None) -> set[str]:
    """The targeted students inside the caller's teaching scope for one of `subject_ids` (only that branch's teaching, if given)."""
    taught = [t for t in p.teaching if t.provider_id == provider_id and t.subject_id in subject_ids and branch_id in (None, t.branch_id)]
    if not taught or not targets:
        return set()
    enrollment_ids = {eid for _sid, eid, _bid in targets}
    branch_of = dict(db.execute(select(Enrollment.enrollment_id, Enrollment.branch_id).where(Enrollment.enrollment_id.in_(enrollment_ids))).all())
    batches: dict[str, set[UUID]] = {}
    for eid, bid in db.execute(select(EnrollmentBatch.enrollment_id, EnrollmentBatch.batch_id).where(EnrollmentBatch.enrollment_id.in_(enrollment_ids))):
        batches.setdefault(eid, set()).add(bid)
    chosen = gen.subjects_taken(db, enrollment_ids)

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


def exam_students(db: Session, p: Principal, exam: Exam, branch_id: UUID | None = None) -> set[str]:
    return matching_students(db, p, exam.provider_id, plan_subjects(db, exam.plan_id), exam_targets(db, exam.exam_id), branch_id)
