"""Teacher-subject assignments (the only way a Teacher reaches branch/subject/batch data)."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, get_principal
from institute_common.database import get_db
from institute_common.models.academics import ProviderSubject, TeacherSubjectAssignment
from institute_common.models.org import Batch, Branch
from institute_common.models.staff import StaffRoleAssignment
from institute_common.schemas.common import Page
from institute_common.util import Strict, paginate

router = APIRouter(prefix="/api/v1", tags=["Teaching"])


class TeachingIn(Strict):
    staff_id: UUID
    branch_id: UUID
    subject_id: UUID
    batch_id: UUID | None = None


class TeachingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    teaching_id: UUID
    staff_id: UUID
    provider_id: UUID
    branch_id: UUID
    subject_id: UUID
    batch_id: UUID | None
    status: str


@router.post("/teaching", response_model=TeachingOut, status_code=201)
def assign_teaching(body: TeachingIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    branch = db.get(Branch, body.branch_id)
    if not branch or branch.deleted_at is not None:
        raise api_error(404, "NOT_FOUND", "Branch not found.")
    p.require(db, "teaching.assign", provider_id=branch.provider_id, branch_id=branch.branch_id)
    teacher_ok = db.scalar(select(StaffRoleAssignment).where(
        StaffRoleAssignment.staff_id == body.staff_id, StaffRoleAssignment.role == "TEACHER",
        StaffRoleAssignment.provider_id == branch.provider_id, StaffRoleAssignment.status == "ACTIVE"))
    if not teacher_ok:
        raise api_error(422, "NOT_A_TEACHER", "Staff member is not a teacher of this provider.")
    if not db.scalar(select(ProviderSubject).where(ProviderSubject.provider_id == branch.provider_id,
                                                  ProviderSubject.subject_id == body.subject_id)):
        raise api_error(422, "SUBJECT_NOT_ENABLED", "Subject is not enabled for this provider.")
    if body.batch_id:
        batch = db.get(Batch, body.batch_id)
        if not batch or batch.branch_id != branch.branch_id:
            raise api_error(422, "INVALID_BATCH", "Batch does not belong to this branch.")
    row = db.scalar(select(TeacherSubjectAssignment).where(
        TeacherSubjectAssignment.staff_id == body.staff_id, TeacherSubjectAssignment.branch_id == body.branch_id,
        TeacherSubjectAssignment.subject_id == body.subject_id,
        TeacherSubjectAssignment.batch_id == body.batch_id if body.batch_id else TeacherSubjectAssignment.batch_id.is_(None)))
    if row and row.status == "ACTIVE":
        raise api_error(409, "ASSIGNMENT_EXISTS", "This teaching assignment already exists.")
    if row:
        row.status = "ACTIVE"
    else:
        row = TeacherSubjectAssignment(staff_id=body.staff_id, provider_id=branch.provider_id, branch_id=body.branch_id,
                                       subject_id=body.subject_id, batch_id=body.batch_id, created_by=p.staff_id)
        db.add(row)
    db.commit()
    audit.record(db, "teaching.assign", "SUCCESS", actor=p.staff_id, target_type="staff", target_id=body.staff_id,
                 provider_id=branch.provider_id, branch_id=branch.branch_id, ip=p.ip, details={"subject_id": str(body.subject_id)})
    return row


@router.get("/teaching", response_model=Page[TeachingOut])
def list_teaching(branch_id: UUID | None = None, staff_id: UUID | None = None, page: int = 1, page_size: int = 25,
                  p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    stmt = select(TeacherSubjectAssignment).where(TeacherSubjectAssignment.status == "ACTIVE")
    if not p.is_global:
        provs = {a.provider_id for a in p.assignments if Role(a.role) == Role.PROVIDER_ADMIN}
        brs = {a.branch_id for a in p.assignments if Role(a.role) == Role.BRANCH_ADMIN}
        stmt = stmt.where(or_(TeacherSubjectAssignment.provider_id.in_(provs) if provs else False,
                              TeacherSubjectAssignment.branch_id.in_(brs) if brs else False,
                              TeacherSubjectAssignment.staff_id == p.staff_id))
    if branch_id:
        stmt = stmt.where(TeacherSubjectAssignment.branch_id == branch_id)
    if staff_id:
        stmt = stmt.where(TeacherSubjectAssignment.staff_id == staff_id)
    return paginate(db, stmt.order_by(TeacherSubjectAssignment.created_at), page, page_size)


@router.delete("/teaching/{teaching_id}", response_model=TeachingOut)
def end_teaching(teaching_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    t = db.get(TeacherSubjectAssignment, teaching_id)
    if not t:
        raise api_error(404, "NOT_FOUND", "Not found.")
    p.require(db, "teaching.assign", provider_id=t.provider_id, branch_id=t.branch_id)
    t.status = "INACTIVE"
    db.commit()
    audit.record(db, "teaching.end", "SUCCESS", actor=p.staff_id, target_type="teaching", target_id=teaching_id,
                 provider_id=t.provider_id, branch_id=t.branch_id, ip=p.ip)
    return t
