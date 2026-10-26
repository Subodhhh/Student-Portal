"""Keeps Institute-side exam assignments in step with what students actually did, using the Student
attempt/result services' internal read APIs, and tells each student once when their result is available.

  attempt exists, IN_PROGRESS            -> STARTED
  attempt exists, SUBMITTED/AUTO_SUBMITTED -> COMPLETED
  no attempt and the window has closed   -> MISSED   (a student who began inside the window is never missed,
                                                       even if the attempt runs past the window end)
Status only moves forward (an out-of-date read cannot undo COMPLETED). Safe to repeat."""
import hashlib
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.database import utcnow
from institute_common.models.exams import Exam, ExamVersion, StudentExamAssignment
from institute_common.clients.attempt_client import AttemptClient, get_attempt_client
from institute_common.internal_http import InternalServiceError
from institute_common.clients.result_client import ResultClient, ResultServiceError, get_result_client
from inst_exam.services.student_notifications import enqueue

log = logging.getLogger("institute.sync")
RANK = {"ASSIGNED": 0, "MISSED": 1, "STARTED": 2, "COMPLETED": 3}
DONE = {"SUBMITTED", "AUTO_SUBMITTED"}


def _latest_version(db: Session, exam_id: UUID) -> ExamVersion | None:
    return db.scalar(select(ExamVersion).where(ExamVersion.exam_id == exam_id).order_by(ExamVersion.version_no.desc()).limit(1))


def sync_exam(db: Session, exam_id, client: AttemptClient | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    exam = db.get(Exam, UUID(str(exam_id)))
    out = {"started": 0, "completed": 0, "missed": 0}
    ev = _latest_version(db, exam.exam_id) if exam else None
    if not exam or exam.status != "PUBLISHED" or not ev or ev.total_questions == 0:
        return out          # a Global Test card without any paper cannot be started: never "missed"
    attempts = {a["student_id"]: a for a in (client or get_attempt_client()).exam_attempts(str(ev.exam_version_id))}
    for a in db.scalars(select(StudentExamAssignment).where(StudentExamAssignment.exam_version_id == ev.exam_version_id)):
        att = attempts.get(a.student_id)
        if att:
            target = "COMPLETED" if att["status"] in DONE else "STARTED"
        elif now >= ev.end_at:
            target = "MISSED"
        else:
            target = "ASSIGNED"
        if RANK[target] > RANK[a.status]:
            a.status, a.status_updated_at = target, now
            out[{"STARTED": "started", "COMPLETED": "completed", "MISSED": "missed"}[target]] += 1
    db.commit()
    return out


def sync_open_exams(db: Session, client: AttemptClient | None = None, now: datetime | None = None) -> dict:
    """Worker step: every published exam that still has assignments that can change."""
    now = now or utcnow()
    totals = {"started": 0, "completed": 0, "missed": 0, "errors": 0}
    exam_ids = db.scalars(
        select(StudentExamAssignment.exam_id).join(ExamVersion, ExamVersion.exam_version_id == StudentExamAssignment.exam_version_id)
        .join(Exam, Exam.exam_id == StudentExamAssignment.exam_id)
        .where(Exam.status == "PUBLISHED", ExamVersion.start_at <= now, ExamVersion.total_questions > 0,
               StudentExamAssignment.status.in_(("ASSIGNED", "STARTED")))
        .distinct().limit(200)
    ).all()
    for eid in exam_ids:
        try:
            for k, v in sync_exam(db, eid, client, now).items():
                totals[k] += v
        except InternalServiceError:
            db.rollback()
            totals["errors"] += 1
            log.warning("attempt sync skipped for exam %s (service unavailable)", eid)
    return totals


def notify_results(db: Session, client: ResultClient | None = None, now: datetime | None = None) -> int:
    """Tell each COMPLETED student, exactly once, that their result is available (never includes the score).
    Returns the number of students newly queued."""
    now = now or utcnow()
    queued = 0
    pending = db.execute(
        select(StudentExamAssignment.exam_version_id).where(StudentExamAssignment.status == "COMPLETED",
                                                            StudentExamAssignment.result_notified_at.is_(None)).distinct().limit(200)
    ).scalars().all()
    for evid in pending:
        ev = db.get(ExamVersion, evid)
        exam = db.get(Exam, ev.exam_id)
        if exam.status != "PUBLISHED":
            continue
        try:
            have = {r["student_id"] for r in (client or get_result_client()).exam_results(str(evid))}
        except ResultServiceError:
            log.warning("result notification skipped for exam version %s (result service unavailable)", evid)
            continue
        rows = [a for a in db.scalars(select(StudentExamAssignment).where(
            StudentExamAssignment.exam_version_id == evid, StudentExamAssignment.status == "COMPLETED",
            StudentExamAssignment.result_notified_at.is_(None))) if a.student_id in have]
        for i in range(0, len(rows), 2000):
            chunk = rows[i:i + 2000]
            ids = sorted(a.student_id for a in chunk)
            key = f"result:{evid}:{hashlib.sha1(','.join(ids).encode(), usedforsecurity=False).hexdigest()[:20]}"
            enqueue(db, dedupe_key=key, event_type="RESULT_PUBLISHED", title=f"Your result is ready: {exam.name}",
                    message="Your test has been evaluated. Open Results to see your score and review your answers.",
                    details={"exam_version_id": str(evid), "test_id": str(evid)}, student_ids=ids, provider_id=exam.provider_id, branch_id=exam.branch_id)
            for a in chunk:
                a.result_notified_at = now
            queued += len(chunk)
        db.commit()
    return queued
