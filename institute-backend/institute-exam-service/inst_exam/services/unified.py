"""Staff access to UNIFIED Global Test exams (one exam per student per occurrence, assembled from several providers' papers).

A unified exam's provider and branch (exam_plans / exams columns, NOT NULL) are only an anchor: they never grant access and never
decide the audience. Access is derived from the manifest's provenance (exam_version_papers.source_submission_id -> the submission's
provider):
  * platform roles (global) see the whole exam: every student, every subject, the overall result;
  * provider staff see ONLY the subjects their provider contributed, and only the assigned students enrolled with that provider
    (provider admin: all of them; branch admin: those enrolled in their branches; teacher: those in their teaching scope for that
    subject). They never see the overall score or another provider's subject;
  * the plan, and every control action (publish, cancel, sync, generate, plan edits), is platform-only.
Exams generated before unified generation have no source_submission_id and keep the ordinary provider/branch rules."""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from institute_common.core.roles import Role
from institute_common.core.security import Principal
from institute_common.models.exams import (Exam, ExamPlanSubject, ExamVersion, ExamVersionPaper, GlobalPaperSubmission, GlobalTestStudent,
                                           StudentExamAssignment)
from institute_common.models.org import Enrollment
from inst_exam.services import teacher_scope


@dataclass(frozen=True)
class SubjectScope:
    """A provider's view of a unified exam: these students, these subjects only."""
    ids: frozenset[str]
    subject_ids: frozenset[UUID]
    # The caller's own papers in this exam and all of its papers (submission ids, as the Student services report them in an
    # attempt's selected_papers). Readers show only attempts that sat one of the caller's papers.
    paper_ids: frozenset[str] = frozenset()
    exam_paper_ids: frozenset[str] = frozenset()

    def sat_mine(self, selected_papers: dict | None) -> bool:
        return bool(papers_sat(selected_papers, self.exam_paper_ids) & self.paper_ids)


def unified_plan_clause(plan_id_column):
    """Per-student rows of a date name the plan, or (dates generated before those rows) its slots come from submissions."""
    return or_(exists().where(GlobalTestStudent.plan_id == plan_id_column),
               exists().where(ExamPlanSubject.plan_id == plan_id_column, ExamPlanSubject.source_submission_id.is_not(None)))


def unified_exam_clause(exam_id_column):
    """Per-student rows of a date name the exam (also an exam without any paper), or its manifest comes from submissions."""
    return or_(exists().where(GlobalTestStudent.exam_id == exam_id_column),
               exists().where(ExamVersion.exam_id == exam_id_column, ExamVersionPaper.exam_version_id == ExamVersion.exam_version_id,
                              ExamVersionPaper.source_submission_id.is_not(None)))


def is_unified_plan(db: Session, plan_id: UUID) -> bool:
    cache = db.info.setdefault("unified_plan", {})           # per session (one request): a plan never changes kind once generated
    if plan_id not in cache:
        cache[plan_id] = bool(db.scalar(select(unified_plan_clause(plan_id))))
    return cache[plan_id]


def prefetch_unified_plans(db: Session, plan_ids) -> None:
    """Which of these plans are unified, in one statement (list pages ask for every plan)."""
    from institute_common.models.exams import ExamPlan
    cache = db.info.setdefault("unified_plan", {})
    wanted = [i for i in plan_ids if i not in cache]
    if wanted:
        found = set(db.scalars(select(ExamPlan.plan_id).where(ExamPlan.plan_id.in_(wanted), unified_plan_clause(ExamPlan.plan_id))))
        cache.update({i: i in found for i in wanted})


def is_unified_exam(db: Session, exam_id: UUID) -> bool:
    cache = db.info.setdefault("unified_exam", {})          # per session (one request): an exam never changes kind once generated
    if exam_id not in cache:
        cache[exam_id] = bool(db.scalar(select(unified_exam_clause(exam_id))))
    return cache[exam_id]


def prefetch_unified_exams(db: Session, exam_ids) -> None:
    """Which of these exams are unified, in one statement (list pages ask for every row)."""
    cache = db.info.setdefault("unified_exam", {})
    wanted = [i for i in exam_ids if i not in cache]
    if wanted:
        found = set(db.scalars(select(Exam.exam_id).where(Exam.exam_id.in_(wanted), unified_exam_clause(Exam.exam_id))))
        cache.update({i: i in found for i in wanted})


def papers_sat(selected_papers: dict | None, exam_paper_ids) -> set[str]:
    """The papers an attempt sat: its snapshot when the student chose papers, otherwise every paper of its exam (an exam made for
    one combination of papers, as every Global Test exam generated before paper choice)."""
    return set((selected_papers or {}).values()) or set(exam_paper_ids)


def exam_papers(db: Session, exam_id: UUID) -> dict[str, UUID]:
    """submission id -> provider of every contributed paper of a unified exam (its frozen manifest)."""
    return {str(sub_id): prov for sub_id, prov in db.execute(
        select(ExamVersionPaper.source_submission_id, GlobalPaperSubmission.provider_id)
        .join(GlobalPaperSubmission, GlobalPaperSubmission.submission_id == ExamVersionPaper.source_submission_id)
        .join(ExamVersion, ExamVersion.exam_version_id == ExamVersionPaper.exam_version_id).where(ExamVersion.exam_id == exam_id))}


def paper_orders(db: Session, exam_version_id: UUID) -> list[tuple[range, ExamVersionPaper, GlobalPaperSubmission | None]]:
    """Each paper of a version with the question orders it holds: orders follow the papers' positions (cumulative counts)."""
    out, start = [], 1
    for evp in db.scalars(select(ExamVersionPaper).where(ExamVersionPaper.exam_version_id == exam_version_id).order_by(ExamVersionPaper.position)):
        sub = db.get(GlobalPaperSubmission, evp.source_submission_id) if evp.source_submission_id else None
        out.append((range(start, start + evp.question_count), evp, sub))
        start += evp.question_count
    return out


def may_correct_own(p: Principal, sub: GlobalPaperSubmission) -> bool:
    """result.correct.own on one contribution of a Global Test: a Provider Admin of its provider; a Branch Admin when EVERY branch of its audience
    is theirs; the Teacher who submitted it, while still allowed to upload papers for that provider."""
    perm = "result.correct.own"
    if p.can(perm, exclude_roles=(Role.BRANCH_ADMIN, Role.TEACHER), provider_id=sub.provider_id):
        return True
    branches = (sub.audience or {}).get("branch_ids") or []
    if branches and all(p.can(perm, exclude_roles=(Role.TEACHER,), provider_id=sub.provider_id, branch_id=UUID(str(b))) for b in branches):
        return True
    return (p.has_role(Role.TEACHER) and sub.submitted_by == p.staff_id and p.can(perm, provider_id=sub.provider_id)
            and p.can("paper.upload", provider_id=sub.provider_id))


def is_platform(p: Principal, perm: str) -> bool:
    return p.is_global and p.has_perm(perm)


def scope(db: Session, p: Principal, perm: str, exam: Exam) -> SubjectScope | None:
    """None = the whole exam (platform roles). Otherwise the caller's subjects and students; empty when they have none. Computed once per
    request (session) for a caller, permission and exam: list pages ask for it more than once per row."""
    if is_platform(p, perm):
        return None
    cache = db.info.setdefault("unified_scope", {})
    key = (p.staff_id, perm, exam.exam_id)
    if key not in cache:
        cache[key] = _scope(db, p, perm, exam)
    return cache[key]


def prefetch_scopes(db: Session, p: Principal, perm: str, exams) -> None:
    """scope() of many unified exams in three statements instead of four per exam (list pages)."""
    if is_platform(p, perm):
        return
    cache = db.info.setdefault("unified_scope", {})
    wanted = [e for e in exams if (p.staff_id, perm, e.exam_id) not in cache]
    for exam_id, sc in _scopes(db, p, perm, [e.exam_id for e in wanted]).items():
        cache[(p.staff_id, perm, exam_id)] = sc


def _scope(db: Session, p: Principal, perm: str, exam: Exam) -> SubjectScope:
    return _scopes(db, p, perm, [exam.exam_id])[exam.exam_id]


def _scopes(db: Session, p: Principal, perm: str, exam_ids: list[UUID]) -> dict[UUID, SubjectScope]:
    """The caller's SubjectScope of each exam: its assigned students, its manifest (submission, provider, subject) and those students'
    enrollments with the contributing providers are read for all exams at once; the rules below are applied per exam."""
    if not exam_ids:
        return {}
    assigned: dict[UUID, set[str]] = {i: set() for i in exam_ids}
    for exam_id, sid in db.execute(select(StudentExamAssignment.exam_id, StudentExamAssignment.student_id)
                                   .where(StudentExamAssignment.exam_id.in_(exam_ids))):
        assigned[exam_id].add(sid)
    manifest: dict[UUID, list[tuple[str, UUID, UUID]]] = {i: [] for i in exam_ids}       # exam -> (submission, provider, subject)
    for exam_id, sub_id, prov, subject in db.execute(
            select(ExamVersion.exam_id, ExamVersionPaper.source_submission_id, GlobalPaperSubmission.provider_id, ExamVersionPaper.subject_id)
            .join(GlobalPaperSubmission, GlobalPaperSubmission.submission_id == ExamVersionPaper.source_submission_id)
            .join(ExamVersion, ExamVersion.exam_version_id == ExamVersionPaper.exam_version_id).where(ExamVersion.exam_id.in_(exam_ids))):
        manifest[exam_id].append((str(sub_id), prov, subject))
    provs = {prov for rows in manifest.values() for _s, prov, _subj in rows}
    students = set().union(*assigned.values())
    enrolled_by: dict[UUID, list[tuple[str, str, UUID]]] = {}
    if provs and students:
        for prov, sid, eid, br in db.execute(select(Enrollment.provider_id, Enrollment.student_id, Enrollment.enrollment_id, Enrollment.branch_id)
                                             .where(Enrollment.provider_id.in_(provs), Enrollment.student_id.in_(students))):
            enrolled_by.setdefault(prov, []).append((sid, eid, br))
    held: list = []

    def memberships():                                   # the teacher check's enrollment reads, once for all these exams (and only if needed)
        if not held:
            mine = {t.provider_id for t in p.teaching}         # a teacher matches only at a provider they teach for
            held.append(teacher_scope._memberships(db, {eid for prov, rows in enrolled_by.items() if prov in mine for _sid, eid, _br in rows}))
        return held[0]
    return {exam_id: _scope_of(db, p, perm, assigned[exam_id], manifest[exam_id], enrolled_by, memberships) for exam_id in exam_ids}


def _scope_of(db: Session, p: Principal, perm: str, assigned: set[str], manifest: list[tuple[str, UUID, UUID]],
              enrolled_by: dict[UUID, list[tuple[str, str, UUID]]], memberships=None) -> SubjectScope:
    students: set[str] = set()
    subjects: set[UUID] = set()
    papers: set[str] = set()
    own: dict[UUID, list[tuple[str, UUID]]] = {}                                 # provider -> its (submission, subject) in this exam
    contributed: dict[UUID, set[UUID]] = {}                                      # provider -> the subjects its papers fill
    for sub_id, prov, subject in manifest:
        own.setdefault(prov, []).append((sub_id, subject))
        contributed.setdefault(prov, set()).add(subject)
    for prov, provided in contributed.items():
        enrolled = [(sid, eid, br) for sid, eid, br in enrolled_by.get(prov, []) if sid in assigned]
        if p.can(perm, exclude_roles=(Role.BRANCH_ADMIN, Role.TEACHER), provider_id=prov):
            mine = {sid for sid, _e, _b in enrolled}
            seen = set(provided)
        else:
            mine = {sid for sid, _e, br in enrolled if p.can(perm, exclude_roles=(Role.TEACHER,), provider_id=prov, branch_id=br)}
            seen = set(provided) if mine else set()
            if p.has_role(Role.TEACHER) and p.can(perm, provider_id=prov):
                for subject in provided:
                    taught = teacher_scope.matching_students(db, p, prov, {subject}, [(sid, eid, None) for sid, eid, _b in enrolled],
                                                             memberships=memberships)
                    if taught:
                        mine |= taught
                        seen.add(subject)
        if mine and seen:
            students |= mine
            subjects |= seen
            papers |= {sub_id for sub_id, subject in own.get(prov, []) if subject in seen}
    return SubjectScope(frozenset(students), frozenset(subjects), frozenset(papers),
                        frozenset(sub_id for subs in own.values() for sub_id, _subject in subs))
