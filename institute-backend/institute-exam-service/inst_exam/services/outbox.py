"""Reliable publication of generated exams to the Student test service (transactional
outbox). Bounded retries with exponential backoff + jitter; permanent failures are
surfaced to staff instead of silently dropped."""
import random
from datetime import timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.database import utcnow
from institute_common.models.academics import Subject
from institute_common.models.exams import Exam, ExamOutbox, ExamPlan, ExamVersion, ExamVersionPaper, StudentExamAssignment
from institute_common.models.papers import PaperVersionQuestion, QuestionVersion
from inst_exam.services.notify import notify, responsible_staff
from inst_exam.services.student_notifications import enqueue as enqueue_student_notification
from inst_exam.services.student_notifications import fmt_ist

MAX_ATTEMPTS = 8


class PermanentPublishError(Exception):
    """Receiver rejected the payload; retrying cannot help."""


class TransientPublishError(Exception):
    pass


class ExamPublisher(Protocol):
    def send(self, payload: dict) -> None: ...


class HttpExamPublisher:
    def __init__(self) -> None:
        if not settings.student_test_service_url or not settings.student_test_service_api_key:
            raise TransientPublishError("Student test service is not configured.")
        self._http = httpx.Client(
            base_url=settings.student_test_service_url.rstrip("/") + "/internal/v1",
            headers={"X-Service-Key": settings.student_test_service_api_key, "X-Service-Name": "institute-backend"},
            timeout=httpx.Timeout(10.0, connect=3.0),
        )

    def send(self, payload: dict) -> None:
        try:
            r = self._http.post("/exams", json=payload, headers={"Idempotency-Key": payload["exam_version_id"] + ":" + payload["operation"]})
        except httpx.TransportError as exc:
            raise TransientPublishError(str(exc)[:200]) from exc
        if r.status_code in (200, 201, 204):
            return
        if r.status_code in (408, 429) or r.status_code >= 500:
            raise TransientPublishError(f"receiver returned {r.status_code}")
        if r.status_code == 409:
            # REFERENCE_MISSING: the provider/branch is not yet in the Student database's reference projection; retry.
            raise TransientPublishError("Student database does not have this provider/branch yet (reference sync pending)")
        raise PermanentPublishError(f"receiver rejected the exam ({r.status_code})")


_publisher: ExamPublisher | None = None


def get_publisher() -> ExamPublisher:
    global _publisher
    return _publisher or HttpExamPublisher()


def set_publisher(p: ExamPublisher | None) -> None:
    global _publisher
    _publisher = p


def build_payload(db: Session, exam_version_id: UUID, operation: str = "PUBLISH") -> dict:
    ev = db.get(ExamVersion, exam_version_id)
    exam = db.get(Exam, ev.exam_id)
    plan = db.get(ExamPlan, exam.plan_id)
    papers = db.scalars(select(ExamVersionPaper).where(ExamVersionPaper.exam_version_id == exam_version_id)
                        .order_by(ExamVersionPaper.position)).all()
    payload = {
        "operation": operation, "exam_version_id": str(ev.exam_version_id), "exam_id": str(exam.exam_id),
        "provider_id": str(exam.provider_id), "branch_id": str(exam.branch_id), "name": exam.name,
        "description": plan.description, "duration_minutes": ev.duration_minutes,
        "start_at": ev.start_at.isoformat(), "end_at": ev.end_at.isoformat(), "questions": [],
        "student_ids": [sid for (sid,) in db.execute(
            select(StudentExamAssignment.student_id).where(StudentExamAssignment.exam_version_id == exam_version_id)
            .order_by(StudentExamAssignment.student_id))],
    }
    sections = (ev.settings or {}).get("sections") or []
    if sections or any(p.source_submission_id for p in papers):   # unified Global Test exam: the student sees it as the date, not a provider's
        payload["global_occurrence_id"] = str(plan.global_occurrence_id)
    if operation == "CANCEL":
        return payload
    # Exam-level marking frozen at generation (exam_versions.settings). PER_QUESTION (the default, and every exam generated before
    # settings existed) keeps each question's own marks; UNIFORM gives every question +correct / -wrong. The Student result service
    # evaluates exactly the marks delivered here, so evaluation always matches the configured scheme.
    marking = (ev.settings or {}).get("marking") or {"mode": "PER_QUESTION"}
    uniform = marking.get("mode") == "UNIFORM"
    order = 0
    for p in papers:
        subject = db.get(Subject, p.subject_id)
        rows = db.execute(
            select(QuestionVersion).join(PaperVersionQuestion, PaperVersionQuestion.question_version_id == QuestionVersion.question_version_id)
            .where(PaperVersionQuestion.paper_version_id == p.paper_version_id).order_by(PaperVersionQuestion.position)
        ).scalars().all()
        for q in rows:
            order += 1
            if uniform:
                marks, negative = int(marking["correct"]), int(marking["wrong"])
            else:
                for label, val in (("marks", q.marks_positive), ("negative_marks", q.marks_negative)):
                    if Decimal(val) != Decimal(val).to_integral_value():
                        raise PermanentPublishError(f"Question marks must be whole numbers for student delivery ({label}={val}).")
                marks, negative = int(q.marks_positive), int(q.marks_negative)
            payload["questions"].append({
                "order": order, "subject": subject.name if subject else None, "text": q.stem,
                "options": {"A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d},
                "correct": q.correct_option, "marks": marks, "negative_marks": negative,
            })
    payload["total_questions"] = len(payload["questions"])
    payload["total_marks"] = sum(q["marks"] for q in payload["questions"])
    if sections:     # every configured subject, in order: its delivered question count, or why the student has no paper for it
        from inst_exam.services.global_layout import message
        delivered: dict[str, int] = {}
        for q in payload["questions"]:
            delivered[q["subject"]] = delivered.get(q["subject"], 0) + 1
        payload["sections"] = [{"position": c["position"], "subject": c["subject"], "status": c["status"],
                                "message": None if c["status"] == "PAPER" else message(c["status"], c["subject"]),
                                "question_count": delivered.get(c["subject"], 0) if c["status"] == "PAPER" else 0} for c in sections]
    return payload


def deliver_due(db: Session, publisher: ExamPublisher | None = None, now=None) -> dict:
    now = now or utcnow()
    stats = {"sent": 0, "retry": 0, "failed": 0}
    rows = db.scalars(select(ExamOutbox).where(ExamOutbox.status == "PENDING", ExamOutbox.next_attempt_at <= now)
                      .order_by(ExamOutbox.created_at).limit(50)).all()
    if rows and publisher is None:
        _refresh_reference_projection()
    for row in rows:
        row.attempts += 1
        try:
            payload = build_payload(db, row.exam_version_id, row.operation)
            (publisher or get_publisher()).send(payload)
            row.status, row.sent_at, row.last_error = "SENT", now, None
            stats["sent"] += 1
            _queue_student_notice(db, row, payload)   # students are told only once the exam is really available
        except (PermanentPublishError, TransientPublishError) as exc:
            row.last_error = str(exc)[:500]
            if isinstance(exc, PermanentPublishError) or row.attempts >= MAX_ATTEMPTS:
                row.status = "FAILED"
                stats["failed"] += 1
                _alert(db, row, str(exc))
            else:
                backoff = min(3600, 30 * 2 ** row.attempts) * (0.5 + random.random())
                row.next_attempt_at = now + timedelta(seconds=backoff)
                stats["retry"] += 1
        db.commit()
    return stats


def _refresh_reference_projection() -> None:
    """Best effort: ask the enrollment service to push providers/branches/enrollments to the Student database first, so a newly
    created branch does not delay its first exam until the next periodic pass."""
    try:
        from institute_common.internal_http import peer
        peer("enrollment", timeout=20.0).request("POST", "/reference-sync")
    except Exception:  # noqa: BLE001 - delivery still proceeds; a missing reference is retried by the outbox
        pass


def _alert(db: Session, row: ExamOutbox, message: str) -> None:
    ev = db.get(ExamVersion, row.exam_version_id)
    exam = db.get(Exam, ev.exam_id)
    notify(db, responsible_staff(db, exam.provider_id, exam.branch_id), kind="EXAM_PUBLISH_FAILED",
           title=f"Exam could not be delivered to students: {exam.name}", body=message,
           dedupe=f"publishfail:{row.outbox_id}", provider_id=exam.provider_id)


def _queue_student_notice(db: Session, row: ExamOutbox, payload: dict) -> None:
    ev = db.get(ExamVersion, row.exam_version_id)
    exam = db.get(Exam, ev.exam_id)
    students = payload.get("student_ids") or [sid for (sid,) in db.execute(
        select(StudentExamAssignment.student_id).where(StudentExamAssignment.exam_version_id == row.exam_version_id))]
    common = dict(student_ids=students, provider_id=exam.provider_id, branch_id=exam.branch_id,
                  details={"exam_version_id": str(row.exam_version_id), "test_id": str(row.exam_version_id)})
    if row.operation == "PUBLISH":
        enqueue_student_notification(
            db, dedupe_key=f"exam-published:{row.exam_version_id}", event_type="TEST_SCHEDULED", title=f"New test scheduled: {exam.name}",
            message=f"You can start it from {fmt_ist(ev.start_at)} until {fmt_ist(ev.end_at)}. Once started you have {ev.duration_minutes} minutes "
                    f"({ev.total_questions} questions).", **common)
    else:
        enqueue_student_notification(
            db, dedupe_key=f"exam-cancelled:{row.exam_version_id}", event_type="SYSTEM_ANNOUNCEMENT", title=f"Test cancelled: {exam.name}",
            message="This test has been cancelled and can no longer be started.", **common)
