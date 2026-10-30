"""Per-student subject layout of a unified Global Test date (pure computation, writes nothing).

Every ELIGIBLE student gets one row per CONFIGURED subject of the test, in the configured order: the paper selected for them, or the
reason there is none. Eligibility reuses the existing rules only:
  * students a submission of the date reaches (its audience with the Create Test rule, the paper's subject and the test's Class; staff-
    listed students are a staff override, as everywhere else), whether or not that paper is usable; and
  * students the existing whole-institute audience rule reaches at a provider offering at least one configured subject (ACTIVE
    enrollment, membership in an ACTIVE batch of an ACTIVE branch, the test's Class), so a student whose institutes submitted nothing
    still gets a card that explains each subject instead of being silently left out.

Status of one (student, subject), first match wins:
  PAPER          exactly one usable submission reaches the student for the subject: that paper is used;
  SELECTABLE     two or more usable submissions reach the student (two teachers of one institute, or two institutes): the student
                 chooses one of them before starting;
  NOT_APPROVED   a submission reaches the student but its paper was not usable (not approved/published by the deadline, or invalid);
  NOT_COVERED    an institute the student takes the subject with submitted a paper for it, but for other batches/students;
  NO_SUBMISSION  the student takes the subject with an institute that submitted nothing for it;
  NOT_ENROLLED   none of the student's institutes teaches them the subject.
"Takes a subject with an institute": the institute offers it and the student has an ACTIVE enrollment there with a membership in an ACTIVE
batch of that enrollment's branch that takes it: the subjects chosen in that membership, or, without a selection, the subjects that batch
offers. An enrollment in no active batch takes no subject (each institute is resolved independently).
Messages never name another institute. (CONFLICT is the status of dates generated before students could choose; it is never produced
any more.)"""
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.models.academics import ProviderSubject
from institute_common.models.exams import GlobalPaperSubmission
from institute_common.models.org import Batch, Enrollment, EnrollmentBatch

MESSAGES = {
    "NOT_ENROLLED": "You are not enrolled in {subject} with any of your institutes.",
    "NO_SUBMISSION": "Your institute has not submitted a {subject} paper for this test.",
    "NOT_APPROVED": "Your institute's {subject} paper was not approved in time for this test.",
    "NOT_COVERED": "No {subject} paper was submitted for your batch or class.",
    "CONFLICT": "{subject} is unavailable because more than one of your institutes submitted a paper for you. Please contact your institute.",
}


def message(status: str, subject: str | None) -> str | None:
    template = MESSAGES.get(status)
    return template.format(subject=subject or "This subject") if template else None


@dataclass
class Cell:
    subject_id: UUID
    position: int
    status: str
    submission: GlobalPaperSubmission | None = None             # PAPER: the one paper
    detail: dict | None = None
    submissions: list[GlobalPaperSubmission] = field(default_factory=list)   # PAPER / SELECTABLE: every eligible paper


@dataclass
class StudentLayout:
    student_id: str
    enrollment_id: str | None
    cells: list[Cell] = field(default_factory=list)

    def key(self) -> tuple:
        """Students with the same key get the same exam: same subjects' papers AND the same reasons for the others."""
        return tuple((c.subject_id, c.status, c.submission.submission_id if c.submission else None) for c in self.cells)

    def papers(self) -> dict[UUID, list[GlobalPaperSubmission]]:
        """subject -> every eligible paper (PAPER and SELECTABLE subjects)."""
        return {c.subject_id: c.submissions for c in self.cells if c.submissions}


def _population(db: Session, offering: dict[UUID, set[UUID]], class_level) -> dict[str, str]:
    """student -> enrollment, for students the whole-institute audience rule reaches at the offering providers."""
    from inst_exam.services.exam_generation import resolve_audience
    out: dict[str, str] = {}
    for provider_id in sorted(offering, key=str):
        for sid, eid, _bid in resolve_audience(db, provider_id, None, "INSTITUTE", [], [], class_level=class_level):
            out.setdefault(sid, eid)
    return out


def _takes(db: Session, students: set[str], offering: dict[UUID, set[UUID]]) -> dict[str, dict[UUID, set[UUID]]]:
    """student -> subject -> providers the student takes it with (see module docstring)."""
    from inst_exam.services.exam_generation import subjects_taken
    from inst_exam.services.exam_generation import batch_subjects
    rows = db.execute(select(Enrollment.student_id, Enrollment.enrollment_id, Enrollment.provider_id)
                      .where(Enrollment.status == "ACTIVE", Enrollment.student_id.in_(list(students) or [""]),
                             Enrollment.provider_id.in_(list(offering) or [None]))).all()
    eids = [eid for _s, eid, _p in rows]
    memberships: dict[str, list[UUID]] = defaultdict(list)       # active batches of the enrollment's own branch only
    for eid, bid in db.execute(select(EnrollmentBatch.enrollment_id, EnrollmentBatch.batch_id)
                               .join(Batch, Batch.batch_id == EnrollmentBatch.batch_id)
                               .join(Enrollment, Enrollment.enrollment_id == EnrollmentBatch.enrollment_id)
                               .where(EnrollmentBatch.enrollment_id.in_(eids or [""]), Batch.status == "ACTIVE",
                                      Batch.branch_id == Enrollment.branch_id)):
        memberships[eid].append(bid)
    chosen = subjects_taken(db, eids)
    offered = batch_subjects(db, {b for bs in memberships.values() for b in bs})
    out: dict[str, dict[UUID, set[UUID]]] = defaultdict(lambda: defaultdict(set))
    for sid, eid, prov in rows:
        taken = set().union(*(chosen.get((eid, b), offered.get(b, set())) for b in memberships.get(eid, []))) if memberships.get(eid) else set()
        for subject in offering[prov] & taken:
            out[sid][subject].add(prov)
    return out


def layouts(db: Session, occurrence_id: UUID, subject_order: list[UUID], class_level, per_provider: dict[UUID, dict],
            papers: dict[str, dict[UUID, list[tuple]]]) -> dict[str, StudentLayout]:
    """The layout of every eligible student (see module docstring). per_provider/papers come from global_generation.assignments/merge
    for the same date."""
    from inst_exam.services.global_generation import resolve_submission
    offering: dict[UUID, set[UUID]] = defaultdict(set)
    for prov, subject in db.execute(select(ProviderSubject.provider_id, ProviderSubject.subject_id)
                                    .where(ProviderSubject.subject_id.in_(subject_order or [None]))):
        offering[prov].add(subject)
    active_subs = db.scalars(select(GlobalPaperSubmission).where(GlobalPaperSubmission.occurrence_id == occurrence_id,
                                                                 GlobalPaperSubmission.status == "ACTIVE")).all()
    submitted = {(s.provider_id, s.subject_id) for s in active_subs}
    enrollment: dict[str, str] = {}
    not_approved: dict[tuple[str, UUID], set[UUID]] = defaultdict(set)
    for prov, a in per_provider.items():
        for sid, per in a["papers"].items():
            for picks in per.values():
                for _sub, eid in picks:
                    enrollment.setdefault(sid, eid)
        for sub in a["not_ready"]:
            for sid, eid, _bid in resolve_submission(db, sub, class_level):
                not_approved[(sid, sub.subject_id)].add(prov)
                enrollment.setdefault(sid, eid)
    for sid, eid in _population(db, offering, class_level).items():
        enrollment.setdefault(sid, eid)
    for sid in papers:
        enrollment.setdefault(sid, None)
    takes = _takes(db, set(enrollment), offering)
    missing = [sid for sid, eid in enrollment.items() if not eid]
    if missing:        # a reference enrollment for the assignment row (it grants nothing): an offering institute's first, else any
        rows = db.execute(select(Enrollment.student_id, Enrollment.enrollment_id, Enrollment.provider_id)
                          .where(Enrollment.status == "ACTIVE", Enrollment.student_id.in_(missing))
                          .order_by(Enrollment.student_id, Enrollment.enrolled_at, Enrollment.enrollment_id)).all()
        for preferred in (True, False):
            for sid, eid, prov in rows:
                if not enrollment[sid] and (prov in offering) == preferred:
                    enrollment[sid] = eid
    out: dict[str, StudentLayout] = {}
    for sid in sorted(enrollment):
        lay = StudentLayout(sid, enrollment[sid])
        mine = papers.get(sid, {})
        for pos, subject in enumerate(subject_order, start=1):
            if subject in mine:
                subs = [sub for sub, _eid in mine[subject]]
                lay.enrollment_id = lay.enrollment_id or mine[subject][0][1]
                if len(subs) == 1:
                    lay.cells.append(Cell(subject, pos, "PAPER", subs[0], submissions=subs))
                else:
                    lay.cells.append(Cell(subject, pos, "SELECTABLE", detail={"providers": sorted({str(x.provider_id) for x in subs})},
                                          submissions=subs))
            elif (sid, subject) in not_approved:
                lay.cells.append(Cell(subject, pos, "NOT_APPROVED", detail={"providers": sorted(str(p) for p in not_approved[(sid, subject)])}))
            elif takes[sid].get(subject):
                status = "NOT_COVERED" if any((p, subject) in submitted for p in takes[sid][subject]) else "NO_SUBMISSION"
                lay.cells.append(Cell(subject, pos, status, detail={"providers": sorted(str(p) for p in takes[sid][subject])}))
            else:
                lay.cells.append(Cell(subject, pos, "NOT_ENROLLED"))
        out[sid] = lay
    return out
