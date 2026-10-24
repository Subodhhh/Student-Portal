"""Student identity matching, enrollments and branch transfers.

Student identities are owned by the Student service: this module only searches and
creates them through its authenticated API and never writes the students table."""
import hashlib
from datetime import datetime
from uuid import UUID, uuid4

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.core.class_levels import LABELS, AcademicYear, check_academic_year, check_profile
from institute_common.models.academics import (BatchSubject, EnrollmentAcademicProfile, EnrollmentBatchSubject, EnrollmentEvent,
                                               EnrollmentTransfer, Subject)
from institute_common.models.exams import Exam, ExamVersion, StudentExamAssignment
from institute_common.models.org import Batch, Branch, Enrollment, EnrollmentBatch, Provider
from institute_common.util import need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from institute_common.clients.student_client import StudentClient, StudentRateLimited, StudentServiceError, get_student_client
from inst_enrollment.services.reference_sync import request_sync

router = APIRouter(prefix="/api/v1", tags=["Students & Enrollments"])


class LookupIn(Strict):
    branch_id: UUID
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def one_key(self):
        if not (self.email or self.phone):
            raise ValueError("email or phone is required")
        return self


class NewStudent(Strict):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)


class BatchSubjectsIn(Strict):
    batch_id: UUID
    subject_ids: list[UUID] = Field(min_length=1, max_length=20)


class AcademicIn(Strict):
    """Class / Academic Level and Academic Year of this provider enrollment (required). See core/class_levels.py."""
    class_level: str = Field(max_length=20)
    academic_year: AcademicYear
    other_class_description: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def valid(self):
        self.class_level, self.other_class_description = check_profile(self.class_level, self.other_class_description)
        return self


class AcademicPatch(Strict):
    """Partial update: an omitted field keeps its stored value; nothing can be cleared (Class and Academic Year are required)."""
    class_level: str | None = Field(default=None, max_length=20)
    academic_year: str | None = Field(default=None, max_length=20)
    other_class_description: str | None = Field(default=None, max_length=200)


class AcademicOut(BaseModel):
    class_level: str
    class_label: str
    academic_year: str
    other_class_description: str | None
    updated_at: datetime | None = None


class EnrollIn(Strict):
    student_id: str | None = Field(default=None, max_length=20)
    new_student: NewStudent | None = None
    confirm_distinct_person: bool = False
    batch_ids: list[UUID] = Field(default_factory=list, max_length=20)
    # Subject-wise enrollment: the subjects taken in some of `batch_ids`; a batch not listed here takes all of its subjects.
    batch_subjects: list[BatchSubjectsIn] = Field(default_factory=list, max_length=20)
    academic: AcademicIn

    @model_validator(mode="after")
    def exactly_one(self):
        if bool(self.student_id) == bool(self.new_student):
            raise ValueError("provide exactly one of student_id or new_student")
        listed = [b.batch_id for b in self.batch_subjects]
        if len(set(listed)) != len(listed) or not set(listed) <= set(self.batch_ids):
            raise ValueError("batch_subjects must name each of batch_ids at most once")
        return self


class SubjectsIn(Strict):
    """The subjects taken in one batch membership; null = every subject the batch offers."""
    subject_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=20)


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    enrollment_id: str
    provider_id: UUID
    branch_id: UUID
    student_id: str
    status: str
    enrolled_at: datetime
    student_name: str | None = None
    batch_ids: list[UUID] = []
    # batch_id -> the subjects chosen in that membership; a batch missing here takes every subject it offers.
    batch_subjects: dict[UUID, list[UUID]] = {}
    # Class and Academic Year of this enrollment; null only for enrollments made before Classes existed (incomplete: set it).
    academic: AcademicOut | None = None


class BatchRef(Strict):
    batch_id: UUID


class StatusIn(Strict):
    status: str = Field(pattern="^(ACTIVE|INACTIVE)$")
    reason: str


class TransferIn(Strict):
    to_branch_id: UUID
    reason: str


class DecisionIn(Strict):
    note: str


class TransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    transfer_id: UUID
    enrollment_id: str
    provider_id: UUID
    student_id: str
    from_branch_id: UUID
    to_branch_id: UUID
    status: str
    reason: str | None
    new_enrollment_id: str | None


def _branch(db: Session, branch_id: UUID) -> Branch:
    b = db.get(Branch, branch_id)
    if not b or b.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    return b


def _enrollment(db: Session, enrollment_id: str) -> Enrollment:
    e = db.get(Enrollment, enrollment_id)
    if not e:
        raise api_error(404, "NOT_FOUND", "Enrollment not found.")
    return e


def _event(db, e: Enrollment, kind: str, actor, reason=None, batch_id=None, branch_id=None):
    db.add(EnrollmentEvent(enrollment_id=e.enrollment_id, provider_id=e.provider_id, branch_id=branch_id or e.branch_id,
                           student_id=e.student_id, event_type=kind, batch_id=batch_id, actor_staff_id=actor, reason=reason))


def _students(sc: StudentClient, call, *a, **kw):
    try:
        return call(*a, **kw)
    except StudentServiceError:
        raise api_error(503, "STUDENT_SERVICE_UNAVAILABLE", "The student service is unavailable. Please retry.")


def _out(e: Enrollment, names: dict, batches: list[UUID], chosen: dict | None = None, profiles: dict | None = None) -> EnrollmentOut:
    s = names.get(e.student_id)
    return EnrollmentOut(enrollment_id=e.enrollment_id, provider_id=e.provider_id, branch_id=e.branch_id,
                         student_id=e.student_id, status=e.status, enrolled_at=e.enrolled_at,
                         student_name=f"{s.first_name} {s.last_name}" if s else None, batch_ids=batches,
                         batch_subjects={bid: sorted(subs, key=str) for (eid, bid), subs in (chosen or {}).items() if eid == e.enrollment_id},
                         academic=_academic((profiles or {}).get(e.enrollment_id)))


def _academic(row: EnrollmentAcademicProfile | None) -> AcademicOut | None:
    return AcademicOut(class_level=row.class_level, class_label=LABELS[row.class_level], academic_year=row.academic_year,
                       other_class_description=row.other_class_description, updated_at=row.updated_at) if row else None


def _profiles(db: Session, enrollment_ids: list[str]) -> dict[str, EnrollmentAcademicProfile]:
    rows = db.scalars(select(EnrollmentAcademicProfile).where(EnrollmentAcademicProfile.enrollment_id.in_(enrollment_ids or [None])))
    return {r.enrollment_id: r for r in rows}


def _describe(row: EnrollmentAcademicProfile | None) -> str:
    if row is None:
        return "not set"
    label = LABELS[row.class_level] + (f" ({row.other_class_description})" if row.other_class_description else "")
    return f"Class {label}, {row.academic_year}"


def _set_profile(db: Session, e: Enrollment, class_level: str, academic_year: str, other: str | None, actor) -> tuple[str, str]:
    """Create or replace the enrollment's academic profile (already validated); history records before and after."""
    row = db.get(EnrollmentAcademicProfile, e.enrollment_id)
    before = _describe(row)
    if row is None:
        row = EnrollmentAcademicProfile(enrollment_id=e.enrollment_id, class_level=class_level, academic_year=academic_year,
                                        other_class_description=other, updated_by=actor)
        db.add(row)
    else:
        row.class_level, row.academic_year, row.other_class_description, row.updated_by = class_level, academic_year, other, actor
    db.flush()
    after = _describe(row)
    _event(db, e, "ACADEMIC_PROFILE_SET", actor, reason=after if before == "not set" else f"{before} -> {after}")
    return before, after


def _chosen(db: Session, enrollment_ids: list[str]) -> dict[tuple[str, UUID], set[UUID]]:
    """(enrollment_id, batch_id) -> the subjects chosen in that membership (memberships without a selection are absent)."""
    out: dict[tuple[str, UUID], set[UUID]] = {}
    for eid, bid, sid in db.execute(
            select(EnrollmentBatch.enrollment_id, EnrollmentBatch.batch_id, EnrollmentBatchSubject.subject_id)
            .join(EnrollmentBatchSubject, EnrollmentBatchSubject.enrollment_batch_id == EnrollmentBatch.enrollment_batch_id)
            .where(EnrollmentBatch.enrollment_id.in_(enrollment_ids or [None]))):
        out.setdefault((eid, bid), set()).add(sid)
    return out


def _check_subjects(db: Session, batch_id: UUID, subject_ids: list[UUID] | None) -> None:
    if subject_ids is None:
        return
    offered = set(db.scalars(select(BatchSubject.subject_id).where(BatchSubject.batch_id == batch_id)))
    if len(set(subject_ids)) != len(subject_ids) or not set(subject_ids) <= offered:
        raise api_error(422, "INVALID_SUBJECT", "Choose subjects that this batch offers, each once.")


def _set_subjects(db: Session, e: Enrollment, link: EnrollmentBatch, subject_ids: list[UUID] | None, actor) -> None:
    """Replace the membership's subject selection (validated by `_check_subjects`) and record it in the enrollment history."""
    for row in db.scalars(select(EnrollmentBatchSubject).where(EnrollmentBatchSubject.enrollment_batch_id == link.enrollment_batch_id)):
        db.delete(row)
    db.flush()
    for sid in subject_ids or []:
        db.add(EnrollmentBatchSubject(enrollment_batch_id=link.enrollment_batch_id, subject_id=sid))
    names = dict(db.execute(select(Subject.subject_id, Subject.name).where(Subject.subject_id.in_(subject_ids or [None]))).all())
    _event(db, e, "BATCH_SUBJECTS_SET", actor, batch_id=link.batch_id,
           reason="All subjects of the batch" if subject_ids is None else "Subjects: " + ", ".join(sorted(names.get(s, str(s)) for s in subject_ids)))


@router.post("/students/lookup")
def lookup_student(body: LookupIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                   sc: StudentClient = Depends(get_student_client)):
    br = _branch(db, body.branch_id)
    p.require(db, "student.create", provider_id=br.provider_id, branch_id=br.branch_id)
    found = _students(sc, sc.search, email=body.email, phone=body.phone)
    audit.record(db, "student.lookup", "SUCCESS", actor=p.staff_id, provider_id=br.provider_id, branch_id=br.branch_id,
                 ip=p.ip, details={"matches": len(found)})
    return {"matches": [s.masked() for s in found]}


@router.post("/branches/{branch_id}/enrollments", response_model=EnrollmentOut, status_code=201)
def enroll(branch_id: UUID, body: EnrollIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
           sc: StudentClient = Depends(get_student_client)):
    br = _branch(db, branch_id)
    p.require(db, "enrollment.manage", provider_id=br.provider_id, branch_id=branch_id)
    if br.status != "ACTIVE":
        raise api_error(409, "BRANCH_INACTIVE", "Branch is inactive.")
    batches = []
    for bid in dict.fromkeys(body.batch_ids):
        b = db.get(Batch, bid)
        if not b or b.branch_id != branch_id or b.status != "ACTIVE":
            raise api_error(422, "INVALID_BATCH", "Batch does not belong to this branch or is inactive.")
        batches.append(bid)
    chosen = {b.batch_id: b.subject_ids for b in body.batch_subjects}
    for bid, subject_ids in chosen.items():
        _check_subjects(db, bid, subject_ids)

    if body.student_id:
        student = _students(sc, sc.get, body.student_id)
        if not student or student.status != "ACTIVE":
            raise api_error(404, "STUDENT_NOT_FOUND", "Student not found.")
    else:
        ns = body.new_student
        email_hits = _students(sc, sc.search, email=ns.email.lower())
        if email_hits:
            # Email is unique per person: link the existing identity instead of duplicating it.
            raise api_error(409, "STUDENT_EXISTS",
                            "A student with this email already exists. Enroll them by Student ID instead.")
        phone_hits = _students(sc, sc.search, phone=ns.phone) if ns.phone else []
        if phone_hits and not body.confirm_distinct_person:
            raise api_error(409, "STUDENT_MATCH_FOUND",
                            "A student with this phone number already exists. Enroll the existing student, "
                            "or confirm this is a different person.")
        key = hashlib.sha256(f"{br.provider_id}:{ns.email.lower()}".encode()).hexdigest()
        student = _students(sc, sc.create, first_name=ns.first_name, last_name=ns.last_name, email=ns.email.lower(),
                            phone=ns.phone, idempotency_key=key)
        audit.record(db, "student.create", "SUCCESS", actor=p.staff_id, target_type="student", target_id=student.student_id,
                     provider_id=br.provider_id, branch_id=branch_id, ip=p.ip)

    dup = db.scalar(select(Enrollment).where(Enrollment.student_id == student.student_id,
                                             Enrollment.branch_id == branch_id, Enrollment.status == "ACTIVE"))
    if dup:
        raise api_error(409, "ALREADY_ENROLLED", "Student is already enrolled in this branch.")
    e = Enrollment(enrollment_id=str(uuid4()), provider_id=br.provider_id, branch_id=branch_id,
                   student_id=student.student_id, status="ACTIVE")
    db.add(e)
    db.flush()
    for bid in batches:
        link = EnrollmentBatch(enrollment_id=e.enrollment_id, batch_id=bid)
        db.add(link)
        db.flush()
        _event(db, e, "BATCH_ADDED", p.staff_id, batch_id=bid)
        if bid in chosen:
            _set_subjects(db, e, link, chosen[bid], p.staff_id)
    _event(db, e, "ENROLLED", p.staff_id)
    ac = body.academic
    _set_profile(db, e, ac.class_level, ac.academic_year, ac.other_class_description, p.staff_id)
    db.commit()
    request_sync()
    details = {"academic": {"class_level": ac.class_level, "academic_year": ac.academic_year}}
    if chosen:
        details["batch_subjects"] = {str(b): [str(s) for s in subs] for b, subs in chosen.items()}
    audit.record(db, "enrollment.create", "SUCCESS", actor=p.staff_id, target_type="enrollment", target_id=e.enrollment_id,
                 provider_id=br.provider_id, branch_id=branch_id, ip=p.ip, details=details)
    return _out(e, {student.student_id: student}, batches, _chosen(db, [e.enrollment_id]), _profiles(db, [e.enrollment_id]))


# ------------------------------------------------------------------ student directory (global identities + their enrollments)

ID_TERM = re.compile(r"^[A-Za-z0-9]{1,20}$")       # letters/digits only: never LIKE wildcards
PHONE = re.compile(r"^\+?[\d\s-]{6,20}$")


def _enrollment_visibility(p: Principal):
    """Enrollments the caller manages: platform roles all; provider admins their providers; branch admins their branches."""
    provs = p.scope_provider_ids("enrollment.manage")
    if provs is None:
        return None
    conds = []
    for prov in provs:
        branches = p.scope_branch_ids("enrollment.manage", prov)
        conds.append(Enrollment.provider_id == prov if branches is None else and_(Enrollment.provider_id == prov, Enrollment.branch_id.in_(branches)))
    return or_(*conds) if conds else false()


def _enrollment_rows(db: Session, student_ids: list[str], visible) -> dict[str, list[dict]]:
    stmt = (select(Enrollment, Provider.name, Branch.name).join(Provider, Provider.provider_id == Enrollment.provider_id)
            .join(Branch, Branch.branch_id == Enrollment.branch_id).where(Enrollment.student_id.in_(student_ids)))
    if visible is not None:
        stmt = stmt.where(visible)
    rows = db.execute(stmt.order_by(Enrollment.enrolled_at)).all()
    batches = db.execute(select(EnrollmentBatch.enrollment_id, Batch.name).join(Batch, Batch.batch_id == EnrollmentBatch.batch_id)
                         .where(EnrollmentBatch.enrollment_id.in_([e.enrollment_id for e, _, _ in rows]))).all() if rows else []
    by_e: dict[str, list[str]] = {}
    for eid, name in batches:
        by_e.setdefault(eid, []).append(name)
    profiles = _profiles(db, [e.enrollment_id for e, _, _ in rows])
    out: dict[str, list[dict]] = {}
    for e, prov_name, br_name in rows:
        ac = _academic(profiles.get(e.enrollment_id))
        out.setdefault(e.student_id, []).append({
            "enrollment_id": e.enrollment_id, "provider_id": str(e.provider_id), "provider_name": prov_name, "branch_id": str(e.branch_id),
            "branch_name": br_name, "status": e.status, "enrolled_at": e.enrolled_at.isoformat(), "batches": sorted(by_e.get(e.enrollment_id, [])),
            "academic": ac.model_dump(mode="json") if ac else None})
    return out


@router.get("/students")
def student_directory(q: str | None = Query(default=None, max_length=100), provider_id: UUID | None = None, branch_id: UUID | None = None,
                      status: str | None = Query(default=None, pattern="^(ACTIVE|INACTIVE)$"), page: int = 1, page_size: int = 25,
                      p: Principal = Depends(get_principal), db: Session = Depends(get_db), sc: StudentClient = Depends(get_student_client)):
    """One row per global student identity that has an enrollment the caller manages, with those enrollments. Identities are never
    created or merged here. Search by Student ID (prefix), email or phone (exact, matched by the Student service)."""
    if not p.has_perm("enrollment.manage"):
        p.require(db, "enrollment.manage")
    stmt = select(Enrollment.student_id)
    visible = _enrollment_visibility(p)
    if visible is not None:
        stmt = stmt.where(visible)
    if provider_id:
        stmt = stmt.where(Enrollment.provider_id == provider_id)
    if branch_id:
        stmt = stmt.where(Enrollment.branch_id == branch_id)
    if status:
        stmt = stmt.where(Enrollment.status == status)
    term = (q or "").strip()
    if term:                                   # blank search = no filter
        if "@" in term:
            matches = _students(sc, sc.search, email=term.lower())
            stmt = stmt.where(Enrollment.student_id.in_([m.student_id for m in matches] or [""]))
        elif PHONE.match(term) or ID_TERM.match(term):
            conds = []
            if ID_TERM.match(term):            # whole or partial Student ID: "STU", "stu00", "0042"
                conds.append(Enrollment.student_id.ilike(f"%{term}%"))
            if PHONE.match(term):              # digits may be a phone number too (exact match, by the Student service)
                conds.append(Enrollment.student_id.in_([m.student_id for m in _students(sc, sc.search, phone=term)] or [""]))
            stmt = stmt.where(or_(*conds))
        else:
            raise api_error(422, "UNSUPPORTED_SEARCH", "Search by Student ID, email or phone number.")
    ids = stmt.distinct().subquery()
    total = db.scalar(select(func.count()).select_from(ids)) or 0
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    page_ids = [sid for (sid,) in db.execute(select(ids.c.student_id).order_by(ids.c.student_id).limit(page_size).offset((page - 1) * page_size))]
    try:
        names = sc.get_many(page_ids)
    except StudentServiceError:
        names = {}   # degrade to IDs, like the branch roster
    enrollments = _enrollment_rows(db, page_ids, visible)
    items = []
    for sid in page_ids:
        s = names.get(sid)
        items.append({"student_id": sid, "name": f"{s.first_name} {s.last_name}" if s else None, "account_status": s.status if s else None,
                      "enrollments": enrollments.get(sid, [])})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/students/{student_id}")
def student_detail(student_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                   sc: StudentClient = Depends(get_student_client)):
    """The global profile (from the Student service) apart from the provider-specific enrollments and exam assignments the caller
    manages. A student with no enrollment in the caller's scope is reported as not found."""
    if not p.has_perm("enrollment.manage"):
        p.require(db, "enrollment.manage")
    visible = _enrollment_visibility(p)
    enrollments = _enrollment_rows(db, [student_id], visible).get(student_id, [])
    if not enrollments:
        raise api_error(404, "NOT_FOUND", "Student not found.")
    s = _students(sc, sc.get, student_id)
    # Only assignments made through an enrollment the caller manages: a branch admin never sees a sibling branch's exams.
    exams = db.execute(
        select(StudentExamAssignment, Exam, ExamVersion).join(Exam, Exam.exam_id == StudentExamAssignment.exam_id)
        .join(ExamVersion, ExamVersion.exam_version_id == StudentExamAssignment.exam_version_id)
        .where(StudentExamAssignment.student_id == student_id,
               StudentExamAssignment.enrollment_id.in_([e["enrollment_id"] for e in enrollments]))
        .order_by(ExamVersion.start_at.desc()).limit(100)).all()
    return {
        "profile": {"student_id": student_id, "first_name": s.first_name if s else None, "last_name": s.last_name if s else None,
                    "email": s.email if s else None, "phone": s.phone if s else None, "account_status": s.status if s else None},
        "enrollments": enrollments,
        "exams": [{"exam_id": str(e.exam_id), "name": e.name, "exam_status": e.status, "assignment_status": a.status,
                   "start_at": v.start_at.isoformat(), "end_at": v.end_at.isoformat()} for a, e, v in exams],
    }


@router.get("/branches/{branch_id}/enrollments", response_model=Page[EnrollmentOut])
def list_enrollments(branch_id: UUID, batch_id: UUID | None = None, status: str | None = None, page: int = 1,
                     page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                     sc: StudentClient = Depends(get_student_client)):
    br = _branch(db, branch_id)
    p.require(db, "enrollment.manage", provider_id=br.provider_id, branch_id=branch_id)
    stmt = select(Enrollment).where(Enrollment.branch_id == branch_id)
    if status in {"ACTIVE", "INACTIVE"}:
        stmt = stmt.where(Enrollment.status == status)
    if batch_id:
        stmt = stmt.where(Enrollment.enrollment_id.in_(select(EnrollmentBatch.enrollment_id).where(EnrollmentBatch.batch_id == batch_id)))
    res = paginate(db, stmt.order_by(Enrollment.enrolled_at.desc()), page, page_size)
    try:
        names = sc.get_many([e.student_id for e in res["items"]])
    except StudentServiceError:
        names = {}  # degrade: show IDs only rather than failing the roster
    links = db.execute(select(EnrollmentBatch.enrollment_id, EnrollmentBatch.batch_id).where(
        EnrollmentBatch.enrollment_id.in_([e.enrollment_id for e in res["items"]]))).all()
    by_e: dict[str, list[UUID]] = {}
    for eid, bid in links:
        by_e.setdefault(eid, []).append(bid)
    ids = [e.enrollment_id for e in res["items"]]
    chosen, profiles = _chosen(db, ids), _profiles(db, ids)
    res["items"] = [_out(e, names, by_e.get(e.enrollment_id, []), chosen, profiles) for e in res["items"]]
    return res


@router.put("/enrollments/{enrollment_id}/batches/{batch_id}", status_code=204)
def add_to_batch(enrollment_id: str, batch_id: UUID, body: SubjectsIn | None = None, p: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    """Add the student to the batch. With a body, also set the subjects taken in it (also for an existing membership)."""
    e = _enrollment(db, enrollment_id)
    p.require(db, "enrollment.manage", provider_id=e.provider_id, branch_id=e.branch_id)
    b = db.get(Batch, batch_id)
    if e.status != "ACTIVE" or not b or b.branch_id != e.branch_id or b.status != "ACTIVE":
        raise api_error(422, "INVALID_BATCH", "Enrollment is inactive, or the batch is not an active batch of this branch.")
    if body:
        _check_subjects(db, batch_id, body.subject_ids)
    link = db.scalar(select(EnrollmentBatch).where(EnrollmentBatch.enrollment_id == enrollment_id, EnrollmentBatch.batch_id == batch_id))
    added = link is None
    if added:
        link = EnrollmentBatch(enrollment_id=enrollment_id, batch_id=batch_id)
        db.add(link)
        db.flush()
        _event(db, e, "BATCH_ADDED", p.staff_id, batch_id=batch_id)
    if body:
        _set_subjects(db, e, link, body.subject_ids, p.staff_id)
    if added or body:
        db.commit()
        request_sync()
    if added:
        audit.record(db, "enrollment.batch_add", "SUCCESS", actor=p.staff_id, target_type="enrollment", target_id=enrollment_id,
                     provider_id=e.provider_id, branch_id=e.branch_id, ip=p.ip)
    if body:
        audit.record(db, "enrollment.batch_subjects", "SUCCESS", actor=p.staff_id, target_type="enrollment", target_id=enrollment_id,
                     provider_id=e.provider_id, branch_id=e.branch_id, ip=p.ip,
                     details={"batch_id": str(batch_id), "subject_ids": None if body.subject_ids is None else [str(s) for s in body.subject_ids]})


@router.delete("/enrollments/{enrollment_id}/batches/{batch_id}", status_code=204)
def remove_from_batch(enrollment_id: str, batch_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _enrollment(db, enrollment_id)
    p.require(db, "enrollment.manage", provider_id=e.provider_id, branch_id=e.branch_id)
    row = db.scalar(select(EnrollmentBatch).where(EnrollmentBatch.enrollment_id == enrollment_id, EnrollmentBatch.batch_id == batch_id))
    if row:
        for chosen in db.scalars(select(EnrollmentBatchSubject).where(EnrollmentBatchSubject.enrollment_batch_id == row.enrollment_batch_id)):
            db.delete(chosen)
        db.flush()
        db.delete(row)
        _event(db, e, "BATCH_REMOVED", p.staff_id, batch_id=batch_id)  # history survives in enrollment_events
        db.commit()
        request_sync()
        audit.record(db, "enrollment.batch_remove", "SUCCESS", actor=p.staff_id, target_type="enrollment", target_id=enrollment_id,
                     provider_id=e.provider_id, branch_id=e.branch_id, ip=p.ip)


@router.patch("/enrollments/{enrollment_id}", response_model=EnrollmentOut)
def set_enrollment_status(enrollment_id: str, body: StatusIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _enrollment(db, enrollment_id)
    p.require(db, "enrollment.manage", provider_id=e.provider_id, branch_id=e.branch_id)
    reason = need_reason(body.reason)
    e.status = body.status
    _event(db, e, f"STATUS_{body.status}", p.staff_id, reason=reason)
    db.commit()
    request_sync()
    audit.record(db, "enrollment.status", "SUCCESS", actor=p.staff_id, target_type="enrollment", target_id=enrollment_id,
                 provider_id=e.provider_id, branch_id=e.branch_id, reason=reason, ip=p.ip)
    return _out(e, {}, [], profiles=_profiles(db, [e.enrollment_id]))


@router.patch("/enrollments/{enrollment_id}/academic-profile", response_model=AcademicOut)
def update_academic_profile(enrollment_id: str, body: AcademicPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Set or change this enrollment's Class / Academic Year. Omitted fields keep their stored values; an enrollment without a profile
    (made before Classes existed) needs class_level and academic_year. Changing away from 'Other' drops its description."""
    e = _enrollment(db, enrollment_id)
    p.require(db, "enrollment.manage", provider_id=e.provider_id, branch_id=e.branch_id)
    row = db.get(EnrollmentAcademicProfile, enrollment_id)
    given = body.model_dump(exclude_unset=True)
    if any(k in given and given[k] is None for k in ("class_level", "academic_year")):
        raise api_error(422, "ACADEMIC_REQUIRED", "Class and Academic Year cannot be cleared.")
    class_level = given.get("class_level", row.class_level if row else None)
    year = given.get("academic_year", row.academic_year if row else None)
    other = given.get("other_class_description", row.other_class_description if row else None)
    if "class_level" in given and "other_class_description" not in given and (class_level or "").strip().upper() != "OTHER":
        other = None          # a new Class other than 'Other' never keeps the old description
    if class_level is None or year is None:
        raise api_error(422, "ACADEMIC_REQUIRED", "This enrollment has no Class yet: give both class_level and academic_year.")
    try:
        code, desc = check_profile(class_level, other)
        year = check_academic_year(year)
    except ValueError as exc:
        raise api_error(422, "INVALID_ACADEMIC_PROFILE", str(exc)) from None
    before, after = _set_profile(db, e, code, year, desc, p.staff_id)
    db.commit()
    request_sync()
    audit.record(db, "enrollment.academic_profile", "SUCCESS", actor=p.staff_id, target_type="enrollment", target_id=enrollment_id,
                 provider_id=e.provider_id, branch_id=e.branch_id, ip=p.ip, details={"before": before, "after": after})
    return _academic(db.get(EnrollmentAcademicProfile, enrollment_id))


@router.post("/enrollments/{enrollment_id}/password-setup-link")
def send_password_setup_link(enrollment_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                             sc: StudentClient = Depends(get_student_client)):
    """Email the enrolled student a password setup link (the Student service's reset-token mechanism; at most 3 per student per
    hour there). Staff who manage this enrollment only; the token never passes through this service."""
    e = _enrollment(db, enrollment_id)
    p.require(db, "enrollment.manage", provider_id=e.provider_id, branch_id=e.branch_id)
    if e.status != "ACTIVE":
        raise api_error(409, "ENROLLMENT_INACTIVE", "Reactivate the enrollment before sending a setup link.")
    try:
        out = _students(sc, sc.send_setup_link, e.student_id)
    except StudentRateLimited as exc:
        audit.record(db, "student.password_setup_link", "FAILURE", actor=p.staff_id, target_type="student", target_id=e.student_id,
                     provider_id=e.provider_id, branch_id=e.branch_id, reason="rate limited (3 per student per hour)", ip=p.ip,
                     details={"enrollment_id": enrollment_id})
        raise api_error(429, "SETUP_LINK_RATE_LIMITED", str(exc)) from None
    if out is None:
        raise api_error(404, "STUDENT_NOT_FOUND", "Student not found or inactive.")
    audit.record(db, "student.password_setup_link", "SUCCESS", actor=p.staff_id, target_type="student", target_id=e.student_id,
                 provider_id=e.provider_id, branch_id=e.branch_id, ip=p.ip,
                 details={"enrollment_id": enrollment_id, "expires_at": out.get("expires_at")})
    return {"student_id": e.student_id, "expires_at": out.get("expires_at")}


# ---------------------------------------------------------------- transfers

@router.post("/enrollments/{enrollment_id}/transfers", response_model=TransferOut, status_code=201)
def request_transfer(enrollment_id: str, body: TransferIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = _enrollment(db, enrollment_id)
    p.require(db, "enrollment.transfer_request", provider_id=e.provider_id, branch_id=e.branch_id)
    reason = need_reason(body.reason)
    target = _branch(db, body.to_branch_id)
    if target.provider_id != e.provider_id or target.branch_id == e.branch_id or target.status != "ACTIVE":
        raise api_error(422, "INVALID_TRANSFER", "Target must be a different, active branch of the same provider.")
    if e.status != "ACTIVE":
        raise api_error(409, "ENROLLMENT_INACTIVE", "Only active enrollments can be transferred.")
    if db.scalar(select(EnrollmentTransfer).where(EnrollmentTransfer.enrollment_id == enrollment_id,
                                                  EnrollmentTransfer.status == "PENDING")):
        raise api_error(409, "TRANSFER_PENDING", "A transfer is already pending for this enrollment.")
    t = EnrollmentTransfer(enrollment_id=enrollment_id, provider_id=e.provider_id, student_id=e.student_id,
                           from_branch_id=e.branch_id, to_branch_id=target.branch_id, reason=reason, requested_by=p.staff_id)
    db.add(t)
    db.commit()
    request_sync()
    audit.record(db, "transfer.request", "SUCCESS", actor=p.staff_id, target_type="transfer", target_id=t.transfer_id,
                 provider_id=e.provider_id, branch_id=e.branch_id, reason=reason, ip=p.ip)
    return t


@router.get("/providers/{provider_id}/transfers", response_model=Page[TransferOut])
def list_transfers(provider_id: UUID, status: str | None = None, page: int = 1, page_size: int = 25,
                   p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    stmt = select(EnrollmentTransfer).where(EnrollmentTransfer.provider_id == provider_id)
    if p.can("enrollment.transfer_approve", provider_id=provider_id):
        pass
    else:
        branches = p.scope_branch_ids("enrollment.transfer_request", provider_id)
        if not branches:
            p.require(db, "enrollment.transfer_approve", provider_id=provider_id)
        stmt = stmt.where(EnrollmentTransfer.from_branch_id.in_(branches))
    if status in {"PENDING", "APPROVED", "REJECTED"}:
        stmt = stmt.where(EnrollmentTransfer.status == status)
    return paginate(db, stmt.order_by(EnrollmentTransfer.created_at.desc()), page, page_size)


def _decide(db, p, transfer_id: UUID, approve: bool, note: str) -> EnrollmentTransfer:
    t = db.get(EnrollmentTransfer, transfer_id)
    if not t:
        raise api_error(404, "NOT_FOUND", "Transfer not found.")
    p.require(db, "enrollment.transfer_approve", provider_id=t.provider_id)
    note = need_reason(note)
    if t.status != "PENDING":
        raise api_error(409, "ALREADY_DECIDED", "This transfer was already decided.")
    t.decided_by, t.decided_at, t.decision_note = p.staff_id, utcnow(), note
    if not approve:
        t.status = "REJECTED"
    else:
        old = _enrollment(db, t.enrollment_id)
        if old.status != "ACTIVE":
            raise api_error(409, "ENROLLMENT_INACTIVE", "Enrollment is no longer active.")
        # History is preserved: the old enrollment is closed, not rewritten.
        old.status = "INACTIVE"
        _event(db, old, "TRANSFERRED_OUT", p.staff_id, reason=note)
        new = Enrollment(enrollment_id=str(uuid4()), provider_id=t.provider_id, branch_id=t.to_branch_id,
                         student_id=t.student_id, status="ACTIVE")
        db.add(new)
        db.flush()
        _event(db, new, "TRANSFERRED_IN", p.staff_id, reason=note)
        prof = db.get(EnrollmentAcademicProfile, old.enrollment_id)
        if prof:   # the student's Class / Academic Year move with them (same provider); the old enrollment keeps its own copy
            _set_profile(db, new, prof.class_level, prof.academic_year, prof.other_class_description, p.staff_id)
        t.status, t.new_enrollment_id = "APPROVED", new.enrollment_id
    db.commit()
    request_sync()
    audit.record(db, "transfer.approve" if approve else "transfer.reject", "SUCCESS", actor=p.staff_id,
                 target_type="transfer", target_id=transfer_id, provider_id=t.provider_id, reason=note, ip=p.ip)
    return t


@router.post("/transfers/{transfer_id}/approve", response_model=TransferOut)
def approve_transfer(transfer_id: UUID, body: DecisionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _decide(db, p, transfer_id, True, body.note)


@router.post("/transfers/{transfer_id}/reject", response_model=TransferOut)
def reject_transfer(transfer_id: UUID, body: DecisionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _decide(db, p, transfer_id, False, body.note)
