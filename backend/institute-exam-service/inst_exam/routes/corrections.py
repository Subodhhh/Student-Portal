"""Result corrections: manual marks adjustment and answer-key correction (with re-evaluation).

Super Admin only (permission `result.correct`). Every request needs an Idempotency-Key and a reason. The request is
stored first (immutable), then applied through the Student services, which keep the immutable result history. A retry
with the same key finishes an interrupted correction using the same correction reference, so nothing is applied twice."""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.exams import Exam, ExamVersion, ResultCorrection, StudentExamAssignment
from institute_common.util import need_reason
from institute_common.util import Strict
from institute_common.clients.answer_key_client import AnswerKeyClient, get_answer_key_client
from institute_common.internal_http import InternalRejected, InternalServiceError
from institute_common.clients.result_client import ResultClient, ResultRejected, ResultServiceError, get_result_client
from inst_exam.services.student_notifications import enqueue as enqueue_student_notification

router = APIRouter(prefix="/api/v1", tags=["Result corrections"])


class AdjustIn(Strict):
    obtained_marks: int
    reason: str = Field(max_length=1000)


class KeyChange(Strict):
    question_order: int = Field(ge=1, le=10000)
    correct_option: str

    @field_validator("correct_option")
    @classmethod
    def letter(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in {"A", "B", "C", "D"}:
            raise ValueError("correct_option must be A, B, C or D")
        return v


class KeyCorrectionIn(Strict):
    changes: list[KeyChange] = Field(min_length=1, max_length=500)
    reason: str = Field(max_length=1000)

    @field_validator("changes")
    @classmethod
    def unique_orders(cls, v: list[KeyChange]):
        if len({c.question_order for c in v}) != len(v):
            raise ValueError("a question may be corrected only once per request")
        return v


def _key(value: str | None) -> str:
    if not value or not (8 <= len(value.strip()) <= 100):
        raise api_error(422, "IDEMPOTENCY_KEY_REQUIRED", "An Idempotency-Key header of 8-100 characters is required.")
    return value.strip()


def _exam_and_version(db: Session, exam_id: UUID, p: Principal, permission: str, **kw) -> tuple[Exam, ExamVersion]:
    e = db.get(Exam, exam_id)
    if not e:
        raise api_error(404, "NOT_FOUND", "Exam not found.")
    if not p.can(permission, provider_id=e.provider_id, branch_id=e.branch_id, **kw):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=e.provider_id, branch_id=e.branch_id,
                     details={"permission": permission})
        raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
    ev = db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam_id).order_by(ExamVersion.version_no.desc()).limit(1))
    return e, ev


def _published(e: Exam, ev: ExamVersion | None) -> ExamVersion:
    if e.status != "PUBLISHED" or not ev:
        raise api_error(409, "NOT_PUBLISHED", "Results can be corrected only for published exams.")
    return ev


def _out(c: ResultCorrection, actor_code: str | None = None) -> dict:
    o = c.outcome or {}
    prev = (o.get("previous") or {}).get("obtained_marks")
    new = (o.get("new") or {}).get("obtained_marks")
    return {"correction_id": str(c.correction_id), "exam_id": str(c.exam_id), "kind": c.kind, "status": c.status, "student_id": c.student_id,
            "reason": c.reason, "actor": actor_code or str(c.actor_staff_id), "created_at": c.created_at.isoformat(),
            "applied_at": c.applied_at.isoformat() if c.applied_at else None, "error": c.error,
            "previous_marks": prev, "new_marks": new, "key_changes": o.get("key_changes"), "reevaluation": o.get("reevaluation")}


def _existing(db: Session, key: str, exam_id: UUID, kind: str, request: dict) -> ResultCorrection | None:
    c = db.scalar(select(ResultCorrection).where(ResultCorrection.idempotency_key == key))
    if c is None:
        return None
    if c.exam_id != exam_id or c.kind != kind or c.request != request:
        raise api_error(409, "IDEMPOTENCY_CONFLICT", "This Idempotency-Key was already used for a different correction.")
    return c


def _fail(db: Session, c: ResultCorrection, message: str, *, status: int, code: str, text: str):
    c.status, c.error = "FAILED", message[:500]
    db.commit()
    audit.record(db, c_action(c), "FAILED", actor=c.actor_staff_id, target_type="exam", target_id=c.exam_id, provider_id=c.provider_id,
                 branch_id=c.branch_id, reason=c.reason, details={"correction_id": str(c.correction_id), "error": message[:200]})
    err = api_error(status, code, text)
    err.detail["correction_id"] = str(c.correction_id)
    return err


def c_action(c: ResultCorrection) -> str:
    return "result.adjust" if c.kind == "MARKS_ADJUSTMENT" else "result.answer_key_correction"


def _rejected(db, c, exc) -> Exception:
    status = 409 if getattr(exc, "status", 422) == 409 else 422
    return _fail(db, c, str(exc), status=status, code="CORRECTION_REJECTED", text="The student service refused the correction.")


def _unavailable(db, c, exc) -> Exception:
    return _fail(db, c, f"{exc}", status=503, code="CORRECTION_PENDING",
                 text="The student service is unavailable. The correction is saved; retry with the same Idempotency-Key to complete it.")


def _finish(db: Session, c: ResultCorrection, p: Principal, e: Exam, changed_students: list[str], title: str) -> None:
    c.status, c.error, c.applied_at = "APPLIED", None, utcnow()
    if changed_students:
        # Never includes the marks: students read their result in the portal.
        enqueue_student_notification(db, dedupe_key=f"correction:{c.correction_id}", event_type="RESULT_PUBLISHED", title=title,
                                     message="Your result for this test was reviewed and updated. Open the test to see the latest result.",
                                     details={"exam_id": str(e.exam_id), "correction_id": str(c.correction_id)},
                                     student_ids=changed_students, provider_id=e.provider_id, branch_id=e.branch_id)
    db.commit()
    audit.record(db, c_action(c), "SUCCESS", actor=p.staff_id, target_type="exam", target_id=e.exam_id, provider_id=e.provider_id,
                 branch_id=e.branch_id, reason=c.reason, ip=p.ip,
                 details={"correction_id": str(c.correction_id), "student_id": c.student_id, "kind": c.kind, "affected": len(changed_students)})


def _new(db: Session, p: Principal, e: Exam, ev: ExamVersion, *, kind: str, student_id: str | None, key: str, request: dict, reason: str) -> ResultCorrection:
    c = ResultCorrection(exam_id=e.exam_id, exam_version_id=ev.exam_version_id, provider_id=e.provider_id, branch_id=e.branch_id, kind=kind,
                         student_id=student_id, idempotency_key=key, request=request, reason=reason, actor_staff_id=p.staff_id)
    db.add(c)
    db.commit()
    return c


def _staff_code(db: Session, p: Principal) -> str:
    from institute_common.models.staff import Staff
    s = db.get(Staff, p.staff_id)
    return s.staff_code if s else str(p.staff_id)


@router.post("/exams/{exam_id}/results/{student_id}/adjustments")
def adjust_result(exam_id: UUID, student_id: str, body: AdjustIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                  p: Principal = Depends(get_principal), db: Session = Depends(get_db), rc: ResultClient = Depends(get_result_client)):
    e, ev = _exam_and_version(db, exam_id, p, "result.correct")
    key = _key(idempotency_key)
    reason = need_reason(body.reason)
    ev = _published(e, ev)
    request = {"student_id": student_id, "obtained_marks": body.obtained_marks}
    c = _existing(db, key, exam_id, "MARKS_ADJUSTMENT", request)
    if c is None:
        if not db.scalar(select(StudentExamAssignment.assignment_id).where(StudentExamAssignment.exam_version_id == ev.exam_version_id,
                                                                           StudentExamAssignment.student_id == student_id)):
            raise api_error(404, "NOT_ASSIGNED", "That student is not assigned to this exam.")
        if abs(Decimal(body.obtained_marks)) > ev.total_marks:
            raise api_error(422, "INVALID_MARKS", f"Marks must be between -{ev.total_marks} and {ev.total_marks}.")
        try:
            row = next((r for r in rc.exam_results(str(ev.exam_version_id)) if r["student_id"] == student_id), None)
        except ResultServiceError:
            raise api_error(503, "RESULT_SERVICE_UNAVAILABLE", "Results are temporarily unavailable.")
        if row is None:
            raise api_error(409, "NO_RESULT", "That student has no result for this exam yet.")
        if row["obtained_marks"] == body.obtained_marks:
            raise api_error(409, "NO_CHANGE", "The student already has these marks.")
        c = _new(db, p, e, ev, kind="MARKS_ADJUSTMENT", student_id=student_id, key=key, request=request, reason=reason)
    if c.status == "APPLIED":
        return _out(c, _staff_code(db, p))
    actor = _staff_code(db, p)
    try:
        outcome = rc.adjust(test_id=str(ev.exam_version_id), student_id=student_id, obtained_marks=body.obtained_marks, reason=c.reason,
                            actor=actor, correction_ref=str(c.correction_id))
    except ResultRejected as exc:
        raise _rejected(db, c, exc)
    except ResultServiceError as exc:
        raise _unavailable(db, c, exc)
    c.outcome = {"previous": outcome.get("previous"), "new": outcome.get("new")}
    _finish(db, c, p, e, [student_id], "Your result was updated")
    return _out(c, actor)


@router.post("/exams/{exam_id}/answer-key-corrections")
def correct_answer_key(exam_id: UUID, body: KeyCorrectionIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                       p: Principal = Depends(get_principal), db: Session = Depends(get_db), rc: ResultClient = Depends(get_result_client),
                       kc: AnswerKeyClient = Depends(get_answer_key_client)):
    e, ev = _exam_and_version(db, exam_id, p, "result.correct")
    key = _key(idempotency_key)
    reason = need_reason(body.reason)
    ev = _published(e, ev)
    changes = sorted(({"question_order": c.question_order, "correct": c.correct_option} for c in body.changes), key=lambda c: c["question_order"])
    request = {"changes": changes}
    c = _existing(db, key, exam_id, "ANSWER_KEY", request)
    if c is None:
        c = _new(db, p, e, ev, kind="ANSWER_KEY", student_id=None, key=key, request=request, reason=reason)
    if c.status == "APPLIED":
        return _out(c, _staff_code(db, p))
    actor = _staff_code(db, p)
    test_id, ref = str(ev.exam_version_id), str(c.correction_id)
    outcome = dict(c.outcome or {})
    try:
        keyed = kc.set_answer_key(test_id=test_id, changes=changes, reason=c.reason, actor=actor, correction_ref=ref)   # idempotent per ref
        outcome["key_changes"] = keyed.get("changes", [])
        c.outcome = outcome
        db.commit()                                         # step 1 is on record even if step 2 fails
    except InternalRejected as exc:
        raise _rejected(db, c, exc)
    except InternalServiceError as exc:
        raise _unavailable(db, c, exc)
    try:
        re = rc.reevaluate(test_id=test_id, reason=c.reason, actor=actor, correction_ref=ref)
    except ResultRejected as exc:
        raise _rejected(db, c, exc)
    except ResultServiceError as exc:
        raise _unavailable(db, c, exc)
    c.outcome = {**outcome, "reevaluation": re}
    _finish(db, c, p, e, list(re.get("changed", [])), "Your result was updated")
    return _out(c, actor)


@router.get("/exams/{exam_id}/result-corrections")
def list_corrections(exam_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    e = db.get(Exam, exam_id)
    if not e:
        raise api_error(404, "NOT_FOUND", "Exam not found.")
    if not p.can("result.read", exclude_roles=(Role.TEACHER,), provider_id=e.provider_id, branch_id=e.branch_id):
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=e.provider_id, branch_id=e.branch_id,
                     details={"permission": "result.read"})
        raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
    rows = db.scalars(select(ResultCorrection).where(ResultCorrection.exam_id == exam_id).order_by(ResultCorrection.created_at.desc()).limit(200)).all()
    from institute_common.models.staff import Staff
    codes = {s.staff_id: s.staff_code for s in db.scalars(select(Staff).where(Staff.staff_id.in_({r.actor_staff_id for r in rows})))} if rows else {}
    return [_out(r, codes.get(r.actor_staff_id)) for r in rows]
