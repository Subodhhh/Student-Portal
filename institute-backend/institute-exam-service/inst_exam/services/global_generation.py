"""Generation of a centrally scheduled Global Test occurrence from the providers' paper submissions: ONE exam for the whole date.

At the submission deadline (start - test.generate_lead_minutes) the worker resolves, per provider, which students each ACTIVE
submission reaches (its audience, resolved from CURRENT enrollments with the Create Test rule), then combines the providers per student:

  * every usable paper that reaches a student for a subject is ELIGIBLE: exactly one makes the subject PAPER; two or more (two teachers
    of one institute, or two institutes) make it SELECTABLE and the student chooses one before starting (never a silent choice);
  * a submission whose paper version is not usable (not APPROVED/PUBLISHED by the deadline, no questions, or marks that are not whole
    numbers under per-question marking) is dropped on its own (NOT_APPROVED for the students it reaches; its provider is told) and never
    blocks the rest of the date;
  * a provider that offers a subject of the test but submitted no usable paper for it is told; no other provider's paper is ever used
    instead of the student's own institutes' papers;
  * every eligible student, with every configured subject's eligible papers or the reason there is none, is frozen ONCE in one plan of
    the date (exam_plans.client_request_id gtocc:<occurrence>): its slots are every usable paper of the date, its audience every eligible
    student. The normal generator makes it ONE exam, one version and one delivery; the Student services serve each student only the
    papers they chose. Students who join later are not added (the date's audience is the snapshot taken at the deadline).

Idempotent: the plan key and the generator's (plan, window) key mean a retried or concurrent run creates nothing twice, and a retry
reuses the frozen plan instead of resolving again. A date stops after MAX_ATTEMPTS failed passes (generation_failed_at) and waits for a
Super Admin's manual retry (retry_generation). Dates generated before paper choice keep their per-group exams unchanged (a date is
generated only once)."""
from collections import defaultdict
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.class_levels import class_target
from institute_common.config import settings
from institute_common.database import utcnow
from institute_common.models.academics import ProviderSubject, Subject
from institute_common.models.exams import (
    Exam, ExamGenerationJob, ExamPlan, ExamPlanStudent, ExamPlanSubject, GlobalPaperSubmission, GlobalTest, GlobalTestOccurrence,
    GlobalTestStudent, GlobalTestSubject, StudentExamAssignment,
)
from institute_common.models.papers import PaperVersion, PaperVersionQuestion, QuestionVersion
from institute_common.models.staff import StaffRoleAssignment
from inst_exam.services import exam_generation as gen
from inst_exam.services import global_layout
from inst_exam.services.notify import notify

READY = {"APPROVED", "PUBLISHED"}
MAX_ATTEMPTS = 3          # generation passes of one date before it waits for a Super Admin's manual retry


class QuestionLimitExceeded(Exception):
    """The date's usable papers hold more questions than one exam may (settings.global_test_max_questions); nothing is truncated."""


def deadline(test: GlobalTest, occ: GlobalTestOccurrence) -> datetime:
    """Papers are accepted (and withdrawn) until this moment; then the occurrence is generated."""
    return occ.start_at - timedelta(minutes=test.generate_lead_minutes)


def submission_class(db: Session, sub: GlobalPaperSubmission) -> tuple[str | None, str | None]:
    """The Global Test's Class for matching: (code or UNMATCHABLE, problem). A legacy label is never guessed: it reaches nobody through
    batches (staff-listed students still count) and the occurrence report names the problem."""
    test = db.get(GlobalTest, db.get(GlobalTestOccurrence, sub.occurrence_id).test_id)
    code, problem = class_target(test.target_class)
    return (gen.UNMATCHABLE if problem else code), problem


_LOOKUP = object()


def resolve_submission(db: Session, sub: GlobalPaperSubmission, class_level=_LOOKUP) -> list[tuple[str, str, UUID | None]]:
    """Students one submission reaches: its audience, its one subject and (by default) its Global Test's Class."""
    from inst_exam.routes.tests import TestAudienceIn, resolve
    if class_level is _LOOKUP:
        class_level, _problem = submission_class(db, sub)
    # once per request (session): a page resolves the same submission for its "students now" and again for coverage, and each
    # resolution is several database round trips; a submission's audience never changes once submitted
    cache = db.info.setdefault("resolved_submissions", {})
    key = (getattr(sub, "submission_id", None), class_level)
    if key[0] is None or key not in cache:
        # Global Tests: a batch membership without a subject selection takes the subjects its batch offers (not every subject)
        rows = resolve(db, TestAudienceIn.model_validate(sub.audience), [sub.subject_id], class_level, unselected_takes_batch_subjects=True)
        if key[0] is None:
            return rows                                       # an unsaved audience (previews, tests): never cached
        cache[key] = rows
    return cache[key]


def unusable(db: Session, test: GlobalTest, version: PaperVersion | None) -> str | None:
    """Why a submitted paper version cannot be delivered (None = usable): not approved/published by the deadline, no questions, or a
    mark that is not a whole number under per-question marking (the student services score whole marks)."""
    from decimal import Decimal
    if not version or version.status not in READY:
        return "NOT_APPROVED"
    if not version.question_count:
        return "NO_QUESTIONS"
    if test.marking_mode != "UNIFORM":
        for plus, minus in db.execute(select(QuestionVersion.marks_positive, QuestionVersion.marks_negative)
                                      .join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
                                      .where(PaperVersionQuestion.paper_version_id == version.paper_version_id)):
            if any(Decimal(v) != Decimal(v).to_integral_value() for v in (plus, minus)):
                return "FRACTIONAL_MARKS"
    return None


def assignments(db: Session, occ: GlobalTestOccurrence, provider_id: UUID) -> dict:
    """Who may sit which papers of one provider: {"papers": {student: {subject: [(submission, enrollment_id), ...]}}, "not_ready":
    [submission], "reasons": {submission_id: why it is unusable}}. Pure computation, writes nothing (also used for coverage before the
    deadline). Two submissions of the provider reaching one student for a subject are both eligible (the student chooses)."""
    loaded = db.info.get("occurrence_submissions", {}).get(occ.occurrence_id)       # the page already read the date's submissions
    if loaded is not None:
        subs = sorted((s for s in loaded if s.provider_id == provider_id and s.status == "ACTIVE"), key=lambda s: s.submitted_at)
    else:
        subs = db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id == occ.occurrence_id,
                                                              GlobalPaperSubmission.provider_id == provider_id,
                                                              GlobalPaperSubmission.status == "ACTIVE")
                          .order_by(GlobalPaperSubmission.submitted_at)).all()
    papers: dict[str, dict[UUID, list[tuple[GlobalPaperSubmission, str]]]] = defaultdict(lambda: defaultdict(list))
    not_ready, reasons = [], {}
    test = db.get(GlobalTest, occ.test_id)
    code, problem = class_target(test.target_class)    # one Class for the whole occurrence
    class_level = gen.UNMATCHABLE if problem else code
    for sub in subs:
        why = unusable(db, test, db.get(PaperVersion, sub.paper_version_id))
        if why:
            not_ready.append(sub)
            reasons[sub.submission_id] = why
            continue
        for sid, eid, _bid in resolve_submission(db, sub, class_level):
            papers[sid][sub.subject_id].append((sub, eid))
    return {"papers": {sid: dict(m) for sid, m in papers.items() if m}, "not_ready": not_ready, "reasons": reasons}


def _provider_admins(db: Session, provider_id: UUID) -> list[UUID]:
    return list(db.scalars(select(StaffRoleAssignment.staff_id).where(StaffRoleAssignment.provider_id == provider_id,
                                                                      StaffRoleAssignment.role == Role.PROVIDER_ADMIN.value,
                                                                      StaffRoleAssignment.status == "ACTIVE")))


def _uncovered(db: Session, provider_id: UUID, covered: set[str]) -> dict[str, int]:
    """Active students of the provider that no submission covers, counted per branch name (who did not get the test at all)."""
    from institute_common.models.org import Branch, Enrollment
    out: dict[str, int] = defaultdict(int)
    seen = set()
    for sid, branch in db.execute(select(Enrollment.student_id, Branch.name).join(Branch, Branch.branch_id == Enrollment.branch_id)
                                  .where(Enrollment.provider_id == provider_id, Enrollment.status == "ACTIVE")):
        if sid not in covered and sid not in seen:
            seen.add(sid)
            out[branch] += 1
    return dict(out)


def merge(per_provider: dict[UUID, dict]) -> dict[str, dict[UUID, list[tuple[GlobalPaperSubmission, str]]]]:
    """Every provider's eligible papers per student and subject, in a stable order (submitted first). Papers of several providers for
    one subject are all eligible: the student chooses (never a CONFLICT)."""
    merged: dict[str, dict[UUID, list]] = defaultdict(lambda: defaultdict(list))
    for a in per_provider.values():
        for sid, per in a["papers"].items():
            for subject, picks in per.items():
                merged[sid][subject].extend(picks)
    return {sid: {subject: sorted(picks, key=lambda x: (x[0].submitted_at, str(x[0].submission_id))) for subject, picks in per.items()}
            for sid, per in merged.items()}


def occurrence_key(occ: GlobalTestOccurrence) -> str:
    """The date's one plan (the gtocc prefix never meets the older per-group gocc/gocc2 keys)."""
    return f"gtocc:{occ.occurrence_id.hex}"


def _record_students(db: Session, occ: GlobalTestOccurrence, plan: ExamPlan, members: list[global_layout.StudentLayout]) -> None:
    """Every student's row of the date (at most one per student: primary key) and one row per configured subject. A student whose
    plan was not generated yet follows the current layout; a student who already holds an exam is never touched."""
    from institute_common.models.exams import GlobalTestStudentSubject as Cell
    from institute_common.models.exams import GlobalTestStudentSubjectPaper as CellPaper
    for lay in members:
        row = db.get(GlobalTestStudent, (occ.occurrence_id, lay.student_id))
        if row is not None and row.exam_id is not None:
            continue
        if row is None:
            db.add(GlobalTestStudent(occurrence_id=occ.occurrence_id, student_id=lay.student_id, enrollment_id=lay.enrollment_id, plan_id=plan.plan_id))
            db.flush()
        else:
            row.plan_id, row.enrollment_id = plan.plan_id, lay.enrollment_id
            for model in (CellPaper, Cell):
                for old in db.scalars(select(model).where(model.occurrence_id == occ.occurrence_id, model.student_id == lay.student_id)):
                    db.delete(old)
                db.flush()
        for c in lay.cells:
            db.add(Cell(occurrence_id=occ.occurrence_id, student_id=lay.student_id, subject_id=c.subject_id, position=c.position,
                        status=c.status, submission_id=c.submission.submission_id if c.submission else None,
                        paper_version_id=c.submission.paper_version_id if c.submission else None,
                        provider_id=c.submission.provider_id if c.submission else None, detail=c.detail))
        db.flush()
        for c in lay.cells:
            for sub in c.submissions:          # every eligible paper of a PAPER / SELECTABLE subject
                db.add(CellPaper(occurrence_id=occ.occurrence_id, student_id=lay.student_id, subject_id=c.subject_id,
                                 submission_id=sub.submission_id, paper_version_id=sub.paper_version_id, provider_id=sub.provider_id))


def _plan_for_occurrence(db: Session, test: GlobalTest, occ: GlobalTestOccurrence, subject_order: list[UUID],
                         members: list[global_layout.StudentLayout]) -> ExamPlan:
    """The date's ONE plan, frozen once: every eligible student and, as slots, every usable paper any of them may sit (configured
    subject order, then submission order). A retry finds it by its key and never resolves the audience again."""
    key = occurrence_key(occ)
    plan = db.scalar(select(ExamPlan).where(ExamPlan.client_request_id == key))
    if plan:
        return plan
    slots: list[GlobalPaperSubmission] = []
    for subject in subject_order:
        seen: dict[UUID, GlobalPaperSubmission] = {}
        for lay in members:
            for c in lay.cells:
                if c.subject_id == subject:
                    seen.update({sub.submission_id: sub for sub in c.submissions})
        slots += sorted(seen.values(), key=lambda x: (x.submitted_at, str(x.submission_id)))
    total = sum(db.get(PaperVersion, sub.paper_version_id).question_count for sub in slots)
    if total > settings.global_test_max_questions:
        raise QuestionLimitExceeded(f"QUESTION_LIMIT_EXCEEDED: the date's {len(slots)} usable paper(s) hold {total} questions; one exam may "
                                    f"hold at most {settings.global_test_max_questions}. Nothing was generated (questions are never cut).")
    if slots:          # the plan is only filed under an anchor: it grants nothing (services/unified.py)
        provider_id, branch_id = slots[0].provider_id, slots[0].branch_id
    else:
        from institute_common.models.org import Enrollment
        e = db.get(Enrollment, members[0].enrollment_id)
        provider_id, branch_id = e.provider_id, e.branch_id
    plan = ExamPlan(provider_id=provider_id, branch_id=branch_id, name=test.name, description=test.description,
                    duration_minutes=test.duration_minutes, start_at=occ.start_at, end_at=occ.end_at, auto_publish=True, status="ACTIVE",
                    audience_type="STUDENTS", marking_mode=test.marking_mode, marks_correct=test.marks_correct, marks_wrong=test.marks_wrong,
                    exam_type=test.exam_type, target_class=test.target_class, global_test_id=test.test_id,
                    global_occurrence_id=occ.occurrence_id, client_request_id=key, created_by=test.created_by)
    db.add(plan)
    try:
        db.flush()
        for lay in members:
            db.add(ExamPlanStudent(plan_id=plan.plan_id, student_id=lay.student_id))
        for pos, sub in enumerate(slots, start=1):
            db.add(ExamPlanSubject(plan_id=plan.plan_id, position=pos, subject_id=sub.subject_id, paper_version_id=sub.paper_version_id,
                                   source_submission_id=sub.submission_id))
        _record_students(db, occ, plan, members)
        db.commit()
    except IntegrityError:          # a concurrent run froze the date first
        db.rollback()
        plan = db.scalar(select(ExamPlan).where(ExamPlan.client_request_id == key))
    return plan


def _contributors(db: Session, occ: GlobalTestOccurrence) -> set[UUID]:
    return set(db.scalars(select(GlobalPaperSubmission.provider_id).where(GlobalPaperSubmission.occurrence_id == occ.occurrence_id,
                                                                          GlobalPaperSubmission.status == "ACTIVE")))


def fail_occurrence(db: Session, test: GlobalTest, occ: GlobalTestOccurrence, error: str) -> None:
    """Stop generating the date (no automatic retry) until a Super Admin retries it; the platform is told the cause and every
    contributing provider that its students have no exam yet (nothing about any other provider)."""
    from inst_exam.routes.global_contributions import _platform_staff
    occ.generation_failed_at, occ.generation_error = utcnow(), error[:1000]
    db.commit()
    when = occ.start_at.strftime("%d %b %Y %H:%M UTC")
    episode = f"gfail:{occ.occurrence_id}:{occ.generation_failed_at.isoformat()}"
    notify(db, _platform_staff(db), kind="GLOBAL_TEST_GENERATION_FAILED", title=f"{test.name} ({when}): exam not generated",
           body=f"Generating this Global Test date stopped after {occ.generation_attempts} attempt(s). A Super Admin can retry it once the "
                f"cause is fixed. Cause: {error[:300]}", dedupe=episode)
    for provider_id in sorted(_contributors(db, occ), key=str):
        notify(db, _provider_admins(db, provider_id), kind="GLOBAL_TEST_GENERATION_FAILED", title=f"{test.name} ({when}): exam not generated",
               body="This Global Test date could not be generated yet, so your students have no exam for it. The platform has been told.",
               dedupe=f"{episode}:{provider_id}", provider_id=provider_id)
    audit.record(db, "global_test.occurrence.generation_failed", "FAILURE", service="exam-scheduler", target_type="global_test_occurrence",
                 target_id=occ.occurrence_id, details={"attempts": occ.generation_attempts, "error": error[:300]})


def retry_generation(db: Session, occ: GlobalTestOccurrence) -> bool:
    """A Super Admin's manual retry of a failed date: clear the stop and the counters (also of the date's failed generation job), so the
    next worker pass resumes. It reuses the frozen plan, so nothing is created twice. False when the date had not failed (no-op)."""
    if occ.generation_failed_at is None:
        return False
    occ.generation_failed_at, occ.generation_error, occ.generation_attempts = None, None, 0
    plan = db.scalar(select(ExamPlan).where(ExamPlan.client_request_id == occurrence_key(occ)))
    if plan:
        for job in db.scalars(select(ExamGenerationJob).where(ExamGenerationJob.plan_id == plan.plan_id, ExamGenerationJob.status == "FAILED")):
            job.attempts = 0
    db.commit()
    return True


def generate_occurrence(db: Session, occ: GlobalTestOccurrence, now: datetime | None = None) -> dict:
    """Generate the date's ONE exam (see module docstring). Returns a report; report["failed"] is non-empty when it must be retried."""
    now = now or utcnow()
    test = db.get(GlobalTest, occ.test_id)
    subject_order = [s for (s,) in db.execute(select(GlobalTestSubject.subject_id).where(GlobalTestSubject.test_id == test.test_id)
                                              .order_by(GlobalTestSubject.position))]
    names = dict(db.execute(select(Subject.subject_id, Subject.name).where(Subject.subject_id.in_(subject_order or [None]))).all())
    report = {"plans": 0, "exams": [], "students": 0, "missing": [], "failed": []}
    _code, class_problem = class_target(test.target_class)
    if class_problem:
        report["class_problem"] = class_problem
    when = occ.start_at.strftime("%d %b %Y %H:%M UTC")

    per_provider: dict[UUID, dict] = {}
    plan = db.scalar(select(ExamPlan).where(ExamPlan.client_request_id == occurrence_key(occ)))
    if plan is None:                                   # not frozen yet: resolve the date's students and papers now
        providers = sorted(_contributors(db, occ), key=str)
        for provider_id in providers:
            try:
                per_provider[provider_id] = assignments(db, occ, provider_id)
            except Exception as exc:  # noqa: BLE001  (the date's audience needs every provider: freeze nothing, retry next pass)
                db.rollback()
                report["failed"].append({"provider_id": str(provider_id), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        if report["failed"]:
            return _finish(db, occ, report, now)
        papers = merge(per_provider)
        holding = set(db.scalars(select(StudentExamAssignment.student_id).join(Exam, Exam.exam_id == StudentExamAssignment.exam_id)
                                 .join(ExamPlan, ExamPlan.plan_id == Exam.plan_id)
                                 .where(ExamPlan.global_occurrence_id == occ.occurrence_id, Exam.status != "CANCELLED")))
        holding |= set(db.scalars(select(GlobalTestStudent.student_id).where(GlobalTestStudent.occurrence_id == occ.occurrence_id,
                                                                             GlobalTestStudent.exam_id.is_not(None))))
        code, problem = class_target(test.target_class)
        lays = global_layout.layouts(db, occ.occurrence_id, subject_order, gen.UNMATCHABLE if problem else code, per_provider, papers)
        members = [lay for sid, lay in sorted(lays.items()) if sid not in holding]     # one exam per student per date, whatever changed
        report["eligible"] = len(lays)
        report["without_paper"] = sum(1 for lay in lays.values() if not lay.papers())
        if members:
            try:
                plan = _plan_for_occurrence(db, test, occ, subject_order, members)
            except QuestionLimitExceeded as exc:       # deterministic: stop now (no retries), never cut questions
                db.rollback()
                report["failed"].append({"error": str(exc)})
                report["question_limit_exceeded"] = True
                occ = db.get(GlobalTestOccurrence, occ.occurrence_id)
                fail_occurrence(db, test, occ, str(exc))
                return report
        report["missing"] = _notify_missing_papers(db, test, occ, subject_order, names, per_provider, when)

    if plan is not None:
        try:
            job = gen.generate(db, plan.plan_id)
        except Exception as exc:  # noqa: BLE001  (recorded and retried on the next pass, within the date's bounded attempts)
            db.rollback()
            report["failed"].append({"plan_id": str(plan.plan_id), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            return _finish(db, occ, report, now)
        if job.status not in ("SUCCEEDED", "PARTIAL") or not job.exam_id:
            # failed, or another (or an interrupted) run holds the job: never mark the date generated while a student lacks the exam;
            # the next pass returns the finished job, or restarts it once it is stale (exam_generation.STALE_RUNNING)
            report["failed"].append({"plan_id": str(plan.plan_id), "error": (job.error or f"generation is still {job.status.lower()}")[:200]})
        else:
            report["plans"] = 1
            report["students"] = db.scalar(select(func.count()).select_from(StudentExamAssignment).where(StudentExamAssignment.exam_id == job.exam_id))
            report["exams"].append(str(job.exam_id))
    return _finish(db, occ, report, now)


def _finish(db: Session, occ: GlobalTestOccurrence, report: dict, now: datetime) -> dict:
    occ = db.get(GlobalTestOccurrence, occ.occurrence_id)
    occ.generation_error = "; ".join(f["error"] for f in report["failed"])[:1000] or None
    if not report["failed"]:
        occ.generated_at = now
    db.commit()
    audit.record(db, "global_test.occurrence.generate", "FAILURE" if report["failed"] else "SUCCESS", service="exam-scheduler",
                 target_type="global_test_occurrence", target_id=occ.occurrence_id,
                 details={k: (v if k != "exams" else len(v)) for k, v in report.items()})
    return report


REASONS = {"NOT_APPROVED": "was not approved by the deadline", "NO_QUESTIONS": "has no questions",
           "FRACTIONAL_MARKS": "has marks that are not whole numbers"}


def _notify_missing_papers(db: Session, test: GlobalTest, occ: GlobalTestOccurrence, subject_order: list[UUID], names: dict,
                           per_provider: dict[UUID, dict], when: str) -> list[dict]:
    """What each provider is missing, told to its admins (never filled with someone else's paper); and providers that offer a subject
    of the test but submitted nothing. Returns the problems per provider (for the report)."""
    missing = []
    for provider_id, a in per_provider.items():
        covered = {subject for per in a["papers"].values() for subject in per}
        offered = {s for (s,) in db.execute(select(ProviderSubject.subject_id).where(ProviderSubject.provider_id == provider_id,
                                                                                   ProviderSubject.subject_id.in_(subject_order)))}
        partial = defaultdict(int)
        for per in a["papers"].values():
            for subject in offered - set(per):
                partial[subject] += 1
        problems = [f"{names.get(s, 'A subject')}: no usable paper was submitted" for s in offered - covered]
        problems += [f"{names.get(s, 'A subject')}: {n} student(s) are not covered by any submission and sit the test without it"
                     for s, n in partial.items() if s in covered]
        problems += [f"A {names.get(sub.subject_id, 'subject')} paper {REASONS.get(a['reasons'].get(sub.submission_id), 'was not usable')} "
                     f"and was not used" for sub in a["not_ready"]]
        uncovered = _uncovered(db, provider_id, set(a["papers"]))
        if uncovered:
            problems.append(f"{sum(uncovered.values())} active student(s) received no paper from you; eligible ones see the test with "
                            f"the reason for each subject ({', '.join(f'{b}: {n}' for b, n in sorted(uncovered.items()))})")
        if problems:
            missing.append({"provider_id": str(provider_id), "problems": problems})
            notify(db, _provider_admins(db, provider_id), kind="GLOBAL_TEST_MISSING_PAPERS", title=f"{test.name} ({when}): papers missing",
                   body="The Global Test was generated for your students with these gaps:\n- " + "\n- ".join(problems),
                   dedupe=f"gmiss:{occ.occurrence_id}:{provider_id}", provider_id=provider_id)
    offering = {p for (p,) in db.execute(select(ProviderSubject.provider_id).where(ProviderSubject.subject_id.in_(subject_order or [None])))}
    for provider_id in sorted(offering - set(per_provider), key=str):
        notify(db, _provider_admins(db, provider_id), kind="GLOBAL_TEST_MISSING_PAPERS", title=f"{test.name} ({when}): no papers submitted",
               body="No paper was submitted for this Global Test, so none of your students received a paper from you; eligible "
                    "students see the test with the reason for each subject.",
               dedupe=f"gmiss:{occ.occurrence_id}:{provider_id}", provider_id=provider_id)
    return missing


def run_due(db: Session, now: datetime | None = None) -> list[dict]:
    """Worker entry point: generate every published date whose submission deadline has passed, that is not generated yet and has not
    stopped. Each pass counts (committed first, so a pass that crashes counts too); after MAX_ATTEMPTS failed passes the date stops and
    waits for a Super Admin's manual retry (never an endless retry)."""
    now = now or utcnow()
    out = []
    rows = db.execute(select(GlobalTestOccurrence, GlobalTest).join(GlobalTest, GlobalTest.test_id == GlobalTestOccurrence.test_id)
                      .where(GlobalTestOccurrence.status == "PUBLISHED", GlobalTestOccurrence.generated_at.is_(None),
                             GlobalTestOccurrence.generation_failed_at.is_(None),
                             GlobalTest.status == "PUBLISHED", GlobalTest.kind == "SCHEDULE")).all()
    for occ, test in rows:
        if deadline(test, occ) > now:
            continue
        occ.generation_attempts += 1
        db.commit()
        try:
            report = generate_occurrence(db, occ, now)
        except Exception as exc:  # noqa: BLE001  (counted; the next pass tries again until the bound)
            db.rollback()
            report = {"failed": [{"error": f"{type(exc).__name__}: {str(exc)[:200]}"}]}
        occ = db.get(GlobalTestOccurrence, occ.occurrence_id)
        if report["failed"] and occ.generated_at is None and occ.generation_failed_at is None and occ.generation_attempts >= MAX_ATTEMPTS:
            fail_occurrence(db, test, occ, report["failed"][0]["error"])
        out.append({"occurrence_id": str(occ.occurrence_id), **report})
    return out
