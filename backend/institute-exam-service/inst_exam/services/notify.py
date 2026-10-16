"""Staff notifications raised by the exam service (missing paper, generation failure, publish failure, ...).

The inbox belongs to the operations service, so the exam service never writes it. A notification is first stored in this
service's `exam_notification_outbox` (same transaction as the event that caused it, idempotent by key) and then delivered to
operations through its internal API; the worker retries what could not be delivered."""
import hashlib
import random
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core.roles import Role
from institute_common.database import utcnow
from institute_common.internal_http import InternalServiceError, peer
from institute_common.models.academics import TeacherSubjectAssignment
from institute_common.models.exams import ExamNotificationOutbox
from institute_common.models.staff import StaffRoleAssignment

MAX_ATTEMPTS = 8


def notify(db: Session, staff_ids, *, kind: str, title: str, body: str, dedupe: str,
           provider_id: UUID | None = None, email: bool = True) -> int:
    ids = sorted({str(s) for s in staff_ids})
    if not ids:
        return 0
    key = f"{dedupe}:{hashlib.sha1(','.join(ids).encode()).hexdigest()[:12]}"[:200]
    if db.scalar(select(ExamNotificationOutbox.outbox_id).where(ExamNotificationOutbox.dedupe_key == key)):
        return 0
    try:
        with db.begin_nested():
            db.add(ExamNotificationOutbox(dedupe_key=key, payload={
                "staff_ids": ids, "kind": kind, "title": title, "body": body, "dedupe": dedupe[:180],
                "provider_id": str(provider_id) if provider_id else None, "email": email}))
    except IntegrityError:
        return 0
    db.commit()
    deliver_pending(db)          # best effort right away; the worker retries failures
    return len(ids)


def deliver_pending(db: Session, now=None, client=None) -> dict:
    now = now or utcnow()
    stats = {"sent": 0, "retry": 0, "failed": 0}
    rows = db.scalars(select(ExamNotificationOutbox).where(ExamNotificationOutbox.status == "PENDING", ExamNotificationOutbox.next_attempt_at <= now)
                      .order_by(ExamNotificationOutbox.created_at).limit(50)).all()
    for row in rows:
        row.attempts += 1
        try:
            resp = (client or peer("operations")).request("POST", "/notifications", json=row.payload)
            if resp.status_code == 200:
                row.status, row.sent_at, row.last_error = "SENT", now, None
                stats["sent"] += 1
                continue
            row.last_error = f"operations answered {resp.status_code}"[:300]
            permanent = 400 <= resp.status_code < 500
        except InternalServiceError as exc:
            row.last_error, permanent = str(exc)[:300], False
        if permanent or row.attempts >= MAX_ATTEMPTS:
            row.status = "FAILED"
            stats["failed"] += 1
        else:
            row.next_attempt_at = now + timedelta(seconds=min(900, 15 * 2 ** row.attempts) * (0.5 + random.random()))
            stats["retry"] += 1
    db.commit()
    return stats


def responsible_staff(db: Session, provider_id: UUID, branch_id: UUID, subject_id: UUID | None = None) -> list[UUID]:
    """Teachers of the subject in that branch + the branch's and provider's admins (read-only lookup)."""
    ids: list[UUID] = []
    if subject_id:
        ids += list(db.scalars(select(TeacherSubjectAssignment.staff_id).where(
            TeacherSubjectAssignment.branch_id == branch_id, TeacherSubjectAssignment.subject_id == subject_id,
            TeacherSubjectAssignment.status == "ACTIVE")))
    rows = db.scalars(select(StaffRoleAssignment).where(StaffRoleAssignment.status == "ACTIVE"))
    for a in rows:
        if (a.role == Role.PROVIDER_ADMIN.value and a.provider_id == provider_id) or (
                a.role == Role.BRANCH_ADMIN.value and a.branch_id == branch_id):
            ids.append(a.staff_id)
    return list(dict.fromkeys(ids))
