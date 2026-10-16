"""Global staff identities, role assignments and teaching assignments."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit, sessions
from app.core.roles import Role
from app.core.security import Principal, api_error, can_grant, get_principal
from app.database import get_db, utcnow
from app.models.academics import ProviderSubject, TeacherSubjectAssignment
from app.models.org import Batch, Branch, Provider
from app.models.staff import Staff, StaffRoleAssignment
from app.routes._util import escape_like, need_reason, paginate
from app.routes.orgs import Strict
from app.schemas.common import Page
from app.services import tokens
from app.services.codes import next_code

router = APIRouter(prefix="/api/v1", tags=["Staff"])


class AssignmentIn(Strict):
    role: Role
    provider_id: UUID | None = None
    branch_id: UUID | None = None


class StaffIn(Strict):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    assignment: AssignmentIn


class StaffPatch(Strict):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=20)


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    assignment_id: UUID
    role: str
    provider_id: UUID | None
    branch_id: UUID | None
    status: str


class StaffOut(BaseModel):
    staff_id: UUID
    staff_code: str
    full_name: str
    email: str
    phone: str | None
    status: str
    email_verified: bool
    assignments: list[AssignmentOut]
    created_at: datetime


class Reason(Strict):
    reason: str


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


def _out(db: Session, s: Staff) -> StaffOut:
    rows = db.scalars(select(StaffRoleAssignment).where(StaffRoleAssignment.staff_id == s.staff_id)).all()
    return StaffOut(
        staff_id=s.staff_id, staff_code=s.staff_code, full_name=s.full_name, email=s.email, phone=s.phone,
        status=s.status, email_verified=s.email_verified_at is not None,
        assignments=[AssignmentOut.model_validate(r) for r in rows], created_at=s.created_at,
    )


def _target_roles(db: Session, staff_id: UUID) -> set[Role]:
    return {Role(r) for r in db.scalars(select(StaffRoleAssignment.role).where(
        StaffRoleAssignment.staff_id == staff_id, StaffRoleAssignment.status == "ACTIVE"))}


def _visible(db: Session, p: Principal, target: Staff) -> bool:
    """Object-level visibility: a scoped actor sees staff holding an assignment in their scope."""
    if p.is_global or target.staff_id == p.staff_id:
        return True
    rows = db.scalars(select(StaffRoleAssignment).where(StaffRoleAssignment.staff_id == target.staff_id)).all()
    mine_p = {a.provider_id for a in p.assignments if Role(a.role) == Role.PROVIDER_ADMIN}
    mine_b = {a.branch_id for a in p.assignments if Role(a.role) == Role.BRANCH_ADMIN}
    teach = db.scalars(select(TeacherSubjectAssignment).where(TeacherSubjectAssignment.staff_id == target.staff_id)).all()
    for r in rows:
        if r.provider_id in mine_p or (r.branch_id and r.branch_id in mine_b):
            return True
    return any(t.branch_id in mine_b for t in teach)


def _guard_target(db: Session, p: Principal, target: Staff) -> None:
    """Hierarchy rules: nobody edits themselves through this path, only a Super Admin may touch
    a Super Admin, and the target must be visible to the actor."""
    if not _visible(db, p, target):
        audit.record(db, "authz.out_of_scope", "DENIED", actor=p.staff_id, target_type="staff", target_id=target.staff_id, ip=p.ip)
        raise api_error(404, "NOT_FOUND", "Staff member not found.")  # do not reveal existence
    if Role.SUPER_ADMIN in _target_roles(db, target.staff_id) and not p.has_role(Role.SUPER_ADMIN):
        audit.record(db, "staff.super_admin_protected", "DENIED", actor=p.staff_id, target_type="staff",
                     target_id=target.staff_id, ip=p.ip)
        raise api_error(403, "FORBIDDEN", "Only a Super Admin may modify a Super Admin.")


def _get_staff(db: Session, staff_id: UUID) -> Staff:
    s = db.get(Staff, staff_id)
    if not s:
        raise api_error(404, "NOT_FOUND", "Staff member not found.")
    return s


def _validate_scope_exists(db: Session, a: AssignmentIn) -> None:
    if a.provider_id:
        prov = db.get(Provider, a.provider_id)
        if not prov or prov.deleted_at is not None or prov.status != "ACTIVE":
            raise api_error(422, "INVALID_SCOPE", "Provider not found or inactive.")
    if a.branch_id:
        br = db.get(Branch, a.branch_id)
        if not br or br.provider_id != a.provider_id or br.deleted_at is not None:
            raise api_error(422, "INVALID_SCOPE", "Branch does not belong to that provider.")


def _add_assignment(db: Session, p: Principal, staff: Staff, a: AssignmentIn) -> StaffRoleAssignment:
    if not can_grant(p, a.role, a.provider_id, a.branch_id):
        audit.record(db, "staff.assign_role", "DENIED", actor=p.staff_id, target_type="staff", target_id=staff.staff_id,
                     provider_id=a.provider_id, branch_id=a.branch_id, ip=p.ip, details={"role": a.role.value})
        raise api_error(403, "FORBIDDEN", "You may not grant this role in this scope.")
    _validate_scope_exists(db, a)
    existing = db.scalar(select(StaffRoleAssignment).where(
        StaffRoleAssignment.staff_id == staff.staff_id, StaffRoleAssignment.role == a.role.value,
        StaffRoleAssignment.provider_id == a.provider_id if a.provider_id else StaffRoleAssignment.provider_id.is_(None),
        StaffRoleAssignment.branch_id == a.branch_id if a.branch_id else StaffRoleAssignment.branch_id.is_(None)))
    if existing:
        if existing.status == "ACTIVE":
            raise api_error(409, "ASSIGNMENT_EXISTS", "This role assignment already exists.")
        existing.status = "ACTIVE"
        row = existing
    else:
        row = StaffRoleAssignment(staff_id=staff.staff_id, role=a.role.value, provider_id=a.provider_id,
                                  branch_id=a.branch_id, created_by=p.staff_id)
        db.add(row)
    db.commit()
    sessions.invalidate_staff_sessions(str(staff.staff_id))  # privilege change => re-authenticate
    audit.record(db, "staff.assign_role", "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff.staff_id,
                 provider_id=a.provider_id, branch_id=a.branch_id, ip=p.ip, details={"role": a.role.value})
    return row


@router.post("/staff", response_model=StaffOut, status_code=201)
def create_staff(body: StaffIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.has_perm("staff.create"):
        p.require(db, "staff.create")
    email = body.email.lower()
    if db.scalar(select(Staff).where(Staff.email == email)):
        # One global identity per person: never create a duplicate; link via the assignment endpoint.
        raise api_error(409, "STAFF_EXISTS", "A staff identity with this email already exists. Look it up and add an assignment.")
    if not can_grant(p, body.assignment.role, body.assignment.provider_id, body.assignment.branch_id):
        audit.record(db, "staff.create", "DENIED", actor=p.staff_id, ip=p.ip,
                     provider_id=body.assignment.provider_id, details={"role": body.assignment.role.value})
        raise api_error(403, "FORBIDDEN", "You may not create staff with this role in this scope.")
    _validate_scope_exists(db, body.assignment)
    staff = None
    for _ in range(5):
        staff = Staff(staff_code=next_code(db, Staff.staff_code, "STF"), email=email, full_name=body.full_name,
                      phone=body.phone, created_by=p.staff_id, created_ip_address=p.ip if p.ip != "unknown" else None)
        db.add(staff)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            if db.scalar(select(Staff).where(Staff.email == email)):
                raise api_error(409, "STAFF_EXISTS", "A staff identity with this email already exists.")
            staff = None
    if staff is None:
        raise api_error(503, "CODE_ALLOCATION_FAILED", "Please retry.")
    a = body.assignment
    db.add(StaffRoleAssignment(staff_id=staff.staff_id, role=a.role.value, provider_id=a.provider_id,
                               branch_id=a.branch_id, created_by=p.staff_id))
    db.commit()
    tokens.issue_token(db, staff, "EMAIL_VERIFY")  # also lets the person set their first password
    audit.record(db, "staff.create", "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff.staff_id,
                 provider_id=a.provider_id, branch_id=a.branch_id, ip=p.ip, details={"role": a.role.value})
    return _out(db, staff)


@router.get("/staff", response_model=Page[StaffOut])
def list_staff(q: str | None = Query(default=None, max_length=100), role: Role | None = None, status: str | None = Query(default=None, pattern="^(ACTIVE|INACTIVE)$"),
               page: int = 1, page_size: int = 25, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.has_perm("staff.read"):
        p.require(db, "staff.read")
    stmt = select(Staff)
    if not p.is_global:
        provs = {a.provider_id for a in p.assignments if Role(a.role) == Role.PROVIDER_ADMIN}
        brs = {a.branch_id for a in p.assignments if Role(a.role) == Role.BRANCH_ADMIN}
        in_scope = select(StaffRoleAssignment.staff_id).where(or_(
            StaffRoleAssignment.provider_id.in_(provs) if provs else False,
            StaffRoleAssignment.branch_id.in_(brs) if brs else False))
        taught = select(TeacherSubjectAssignment.staff_id).where(
            TeacherSubjectAssignment.branch_id.in_(brs) if brs else False)
        stmt = stmt.where(or_(Staff.staff_id.in_(in_scope), Staff.staff_id.in_(taught)))
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Staff.full_name.ilike(like, escape="\\"), Staff.email.ilike(like, escape="\\"),
                              Staff.staff_code.ilike(like, escape="\\")))
    if status:
        stmt = stmt.where(Staff.status == status)
    if role:
        stmt = stmt.where(Staff.staff_id.in_(select(StaffRoleAssignment.staff_id).where(
            StaffRoleAssignment.role == role.value, StaffRoleAssignment.status == "ACTIVE")))
    res = paginate(db, stmt.order_by(Staff.staff_code), page, page_size)
    res["items"] = [_out(db, s) for s in res["items"]]
    return res


@router.get("/staff/lookup", response_model=list[dict])
def lookup_staff(email: EmailStr, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Find an existing global identity so an assignment can be added instead of duplicating it.
    Returns only identifying minimum, and only to actors who can assign roles."""
    if not p.has_perm("staff.assign_role"):
        p.require(db, "staff.assign_role")
    s = db.scalar(select(Staff).where(Staff.email == email.lower()))
    return [{"staff_id": str(s.staff_id), "staff_code": s.staff_code, "full_name": s.full_name}] if s else []


@router.get("/staff/{staff_id}", response_model=StaffOut)
def get_staff(staff_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = _get_staff(db, staff_id)
    if not _visible(db, p, s) or not (p.is_global or s.staff_id == p.staff_id or p.has_role(Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN)):
        audit.record(db, "authz.out_of_scope", "DENIED", actor=p.staff_id, target_type="staff", target_id=staff_id, ip=p.ip)
        raise api_error(404, "NOT_FOUND", "Staff member not found.")
    return _out(db, s)


@router.patch("/staff/{staff_id}", response_model=StaffOut)
def update_staff(staff_id: UUID, body: StaffPatch, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = _get_staff(db, staff_id)
    if s.staff_id != p.staff_id:
        if not any(a for a in p.assignments if Role(a.role) in {Role.SUPER_ADMIN, Role.ADMIN, Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN}):
            raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
        _guard_target(db, p, s)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    s.updated_by = p.staff_id
    db.commit()
    audit.record(db, "staff.update", "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff_id, ip=p.ip)
    return _out(db, s)


@router.post("/staff/{staff_id}/assignments", response_model=AssignmentOut, status_code=201)
def add_assignment(staff_id: UUID, body: AssignmentIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = _get_staff(db, staff_id)
    if s.staff_id == p.staff_id:
        raise api_error(403, "FORBIDDEN", "You cannot change your own roles.")
    if s.status != "ACTIVE":
        raise api_error(409, "STAFF_INACTIVE", "Reactivate the staff member first.")
    # Linking an existing identity requires only role-grant rights, not prior visibility.
    if Role.SUPER_ADMIN in _target_roles(db, s.staff_id) and not p.has_role(Role.SUPER_ADMIN):
        raise api_error(403, "FORBIDDEN", "Only a Super Admin may modify a Super Admin.")
    return _add_assignment(db, p, s, body)


@router.delete("/staff/{staff_id}/assignments/{assignment_id}", response_model=AssignmentOut)
def end_assignment(staff_id: UUID, assignment_id: UUID, body: Reason, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = _get_staff(db, staff_id)
    a = db.get(StaffRoleAssignment, assignment_id)
    if not a or a.staff_id != s.staff_id:
        raise api_error(404, "NOT_FOUND", "Assignment not found.")
    if s.staff_id == p.staff_id:
        raise api_error(403, "FORBIDDEN", "You cannot change your own roles.")
    reason = need_reason(body.reason)
    if Role(a.role) == Role.SUPER_ADMIN and not p.has_role(Role.SUPER_ADMIN):
        raise api_error(403, "FORBIDDEN", "Only a Super Admin may modify a Super Admin.")
    if not can_grant(p, Role(a.role), a.provider_id, a.branch_id):
        audit.record(db, "staff.end_assignment", "DENIED", actor=p.staff_id, target_type="staff", target_id=staff_id, ip=p.ip)
        raise api_error(403, "FORBIDDEN", "You may not modify this assignment.")
    a.status = "INACTIVE"
    # Removing a scope also retires the teaching assignments that depended on it.
    if a.role == Role.TEACHER.value:
        for t in db.scalars(select(TeacherSubjectAssignment).where(
                TeacherSubjectAssignment.staff_id == s.staff_id, TeacherSubjectAssignment.provider_id == a.provider_id)):
            t.status = "INACTIVE"
    db.commit()
    sessions.invalidate_staff_sessions(str(s.staff_id))
    audit.record(db, "staff.end_assignment", "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff_id,
                 provider_id=a.provider_id, branch_id=a.branch_id, reason=reason, ip=p.ip)
    return a


def _identity_status(db: Session, p: Principal, staff_id: UUID, status: str, reason: str, action: str) -> StaffOut:
    # Identity-wide changes affect every provider the person works for => global roles only.
    if not p.is_global:
        raise api_error(403, "FORBIDDEN", "Only platform staff can change a global identity's status.")
    reason = need_reason(reason)
    s = _get_staff(db, staff_id)
    if s.staff_id == p.staff_id:
        raise api_error(403, "FORBIDDEN", "You cannot change your own status.")
    _guard_target(db, p, s)
    s.status = status
    s.deactivated_at = utcnow() if status == "INACTIVE" else None
    s.updated_by = p.staff_id
    db.commit()
    sessions.invalidate_staff_sessions(str(s.staff_id))
    audit.record(db, action, "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff_id, reason=reason, ip=p.ip)
    return _out(db, s)


@router.post("/staff/{staff_id}/deactivate", response_model=StaffOut)
def deactivate_staff(staff_id: UUID, body: Reason, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _identity_status(db, p, staff_id, "INACTIVE", body.reason, "staff.deactivate")


@router.post("/staff/{staff_id}/reactivate", response_model=StaffOut)
def reactivate_staff(staff_id: UUID, body: Reason, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _identity_status(db, p, staff_id, "ACTIVE", body.reason, "staff.reactivate")


@router.post("/staff/{staff_id}/resend-verification", status_code=202)
def resend_verification(staff_id: UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    s = _get_staff(db, staff_id)
    _guard_target(db, p, s)
    if not any(r in {Role.SUPER_ADMIN, Role.ADMIN, Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN} for r in p.roles):
        raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")
    if s.email_verified_at is not None and s.password_hash is not None:
        raise api_error(409, "ALREADY_VERIFIED", "Email is already verified.")
    tokens.issue_token(db, s, "EMAIL_VERIFY")
    audit.record(db, "staff.resend_verification", "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff_id, ip=p.ip)
    return {"message": "Verification email sent."}


# --------------------------------------------------------- teaching assignments

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
