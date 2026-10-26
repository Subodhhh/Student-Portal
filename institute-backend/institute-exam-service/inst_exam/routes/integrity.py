"""Exam integrity for staff: investigate attempts with browser warnings (and automatic submissions after warnings), and decide a
student's review request.

Visibility follows the exam's results exactly (exam_access with result.read): platform roles see every student; provider and branch
staff only their own students; a provider's view of a unified Global Test exam only the students of its contributed subjects.
Deciding is narrower: the Platform Admin, or a Provider Admin of a provider the student is enrolled with (for a unified exam, one that
contributed to it). Branch admins and teachers may look, not decide. The attempt service keeps the records and the decision; nothing
here changes an attempt's answers or result. Browser events are evidence of activity in the browser, not proof of intent."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.clients.attempt_client import AttemptClient, get_attempt_client
from institute_common.clients.student_client import StudentClient, StudentServiceError, get_student_client
from institute_common.core import audit
from institute_common.core.roles import GLOBAL_ROLES, Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db
from institute_common.internal_http import InternalServiceError
from institute_common.models.exams import ExamVersion, StudentExamAssignment
from institute_common.models.org import Enrollment
from institute_common.util import Strict
from inst_exam.routes.exams import _exam, _in_branches, exam_access
from inst_exam.services import unified

router = APIRouter(prefix="/api/v1", tags=["Exam integrity"])


def _context(db: Session, p: Principal, exam_id: UUID):
    e = _exam(db, exam_id)
    scope = exam_access(db, p, "result.read", e)
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    if not ev:
        raise api_error(404, "NOT_FOUND", "This exam has no version yet.")
    stmt = select(StudentExamAssignment.student_id).where(StudentExamAssignment.exam_version_id == ev.exam_version_id)
    if scope is not None:
        stmt = stmt.where(_in_branches(scope))
    return e, ev, scope, set(db.scalars(stmt))


def _unavailable():
    return api_error(503, "ATTEMPT_SERVICE_UNAVAILABLE", "Attempt records are temporarily unavailable.")


@router.get("/exams/{exam_id}/integrity")
def exam_integrity(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                   ac: AttemptClient = Depends(get_attempt_client), sc: StudentClient = Depends(get_student_client)):
    """Attempts of this exam, among the caller's students, with counted warnings, an automatic submission after warnings, a review
    request or an authorized retake."""
    e, ev, _scope, allowed = _context(db, p, exam_id)
    try:
        attempts = ac.exam_attempts(str(ev.exam_version_id))
        reviews = {r["attempt_id"]: r for r in ac.test_reviews(str(ev.exam_version_id))}
    except InternalServiceError:
        raise _unavailable()
    rows = [a for a in attempts if a["student_id"] in allowed
            and (a.get("warnings") or a.get("submit_reason") == "WARNING_THRESHOLD" or a.get("retake_of_attempt_id") or a["attempt_id"] in reviews)]
    try:
        names = sc.get_many(sorted({a["student_id"] for a in rows})[:200])
    except StudentServiceError:
        names = {}
    can_decide = {a["student_id"]: _may_decide(db, p, e, a["student_id"]) for a in rows}
    return {"exam_id": str(exam_id), "items": [{
        **{k: a.get(k) for k in ("student_id", "attempt_id", "status", "started_at", "submitted_at", "submit_reason", "review_status",
                                 "warnings", "retake_of_attempt_id")},
        "name": f"{names[a['student_id']].first_name} {names[a['student_id']].last_name}".strip() if a["student_id"] in names else None,
        "review": reviews.get(a["attempt_id"]), "can_decide": can_decide[a["student_id"]]} for a in rows]}


@router.get("/exams/{exam_id}/integrity/{attempt_id}")
def attempt_integrity(exam_id: UUID, attempt_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
                      ac: AttemptClient = Depends(get_attempt_client)):
    """The full record of one attempt: every reported event (counted or not), the automatic submission, the review and any retake."""
    e, ev, _scope, allowed = _context(db, p, exam_id)
    detail = _attempt_of(ac, ev, attempt_id, allowed)
    return {**detail, "can_decide": _may_decide(db, p, e, detail["student_id"])}


class DecisionIn(Strict):
    decision: str = Field(pattern="^(RETAKE|UPHOLD)$")
    reason: str = Field(min_length=5, max_length=2000)


@router.post("/exams/{exam_id}/integrity/{attempt_id}/decision")
def decide(exam_id: UUID, attempt_id: UUID, body: DecisionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db),
           ac: AttemptClient = Depends(get_attempt_client)):
    """RETAKE authorizes ONE new, separate attempt for this student and exam (the original stays as it is, permanently); UPHOLD keeps
    the automatic submission. Decided once."""
    e, ev, _scope, allowed = _context(db, p, exam_id)
    detail = _attempt_of(ac, ev, attempt_id, allowed)
    if not _may_decide(db, p, e, detail["student_id"]):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "integrity.decide", "exam_id": str(exam_id)})
        raise api_error(403, "FORBIDDEN", "Only the Platform Admin or a Provider Admin of the student's institute can decide a review.")
    try:
        status, out = ac.review_decision(str(attempt_id), {"decision": body.decision, "reason": body.reason.strip(), "staff_id": str(p.staff_id),
                                                           "staff_name": p.staff.full_name})
    except InternalServiceError:
        raise _unavailable()
    if status == 409:
        raise api_error(409, "ALREADY_DECIDED", out.get("detail", "This review was already decided."))
    if status != 200:
        raise api_error(status, "REVIEW_REFUSED", out.get("detail", "The review could not be decided."))
    audit.record(db, "exam.integrity.decide", "SUCCESS", actor=p.staff_id, target_type="attempt", target_id=attempt_id,
                 provider_id=e.provider_id, branch_id=e.branch_id, reason=body.reason.strip(), ip=p.ip,
                 details={"exam_id": str(exam_id), "student_id": detail["student_id"], "decision": body.decision})
    return out


def _attempt_of(ac: AttemptClient, ev: ExamVersion, attempt_id: UUID, allowed: set[str]) -> dict:
    try:
        detail = ac.attempt_integrity(str(attempt_id))
    except InternalServiceError:
        raise _unavailable()
    # an attempt of another exam, or of a student outside the caller's scope, does not exist for the caller
    if not detail or str(detail["test_id"]) != str(ev.exam_version_id) or detail["student_id"] not in allowed:
        raise api_error(404, "NOT_FOUND", "Attempt not found.")
    return detail


def _may_decide(db: Session, p: Principal, e, student_id: str) -> bool:
    if p.is_global and any(Role(a.role) in GLOBAL_ROLES for a in p.assignments):
        return True
    admin_of = {a.provider_id for a in p.assignments if Role(a.role) == Role.PROVIDER_ADMIN}
    if not admin_of:
        return False
    providers = set(unified.contributions(db, e.exam_id)) if unified.is_unified_exam(db, e.exam_id) else {e.provider_id}
    enrolled = set(db.scalars(select(Enrollment.provider_id).where(Enrollment.student_id == student_id, Enrollment.status == "ACTIVE")))
    return bool(admin_of & providers & enrolled)
