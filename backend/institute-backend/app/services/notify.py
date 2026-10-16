"""Idempotent staff notifications. The dedupe key makes retries (and replayed jobs) safe:
the same event never produces a second notification or email."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.email import Mail, get_sender
from app.core.roles import Role
from app.models.academics import TeacherSubjectAssignment
from app.models.exams import Notification
from app.models.staff import Staff, StaffRoleAssignment


def notify(db: Session, staff_ids, *, kind: str, title: str, body: str, dedupe: str,
           provider_id: UUID | None = None, email: bool = True) -> int:
    sent = 0
    for sid in dict.fromkeys(staff_ids):
        key = f"{dedupe}:{sid}"
        n = Notification(recipient_staff_id=sid, provider_id=provider_id, kind=kind, title=title, body=body, dedupe_key=key)
        db.add(n)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            continue  # already delivered for this event
        sent += 1
        if email:
            staff = db.get(Staff, sid)
            if staff and staff.status == "ACTIVE" and staff.email_verified_at is not None:
                try:
                    get_sender().send(Mail(to=staff.email, subject=title, body=body))
                    n.email_status = "SENT"
                except Exception:
                    n.email_status = "FAILED"  # visible for reconciliation; never blocks the workflow
    db.commit()
    return sent


def responsible_staff(db: Session, provider_id: UUID, branch_id: UUID, subject_id: UUID | None = None) -> list[UUID]:
    """Teachers of the subject in that branch + the branch's and provider's admins."""
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
