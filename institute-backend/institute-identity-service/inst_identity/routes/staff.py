"""Global staff identities, role assignments and teaching assignments."""
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from institute_common.core import audit, sessions
from institute_common.core.roles import Role
from institute_common.core.security import Principal, api_error, can_grant, can_see_provider, get_principal
from institute_common.database import get_db, utcnow
from institute_common.models.academics import TeacherSubjectAssignment
from institute_common.models.org import Branch, Provider
from institute_common.models.staff import AuditLog, Staff, StaffRoleAssignment
from institute_common.util import escape_like, need_reason, paginate
from institute_common.util import Strict
from institute_common.schemas.common import Page
from inst_identity.services import tokens
from institute_common.codes import next_code
from institute_common.internal_http import InternalServiceError, peer


log = logging.getLogger(__name__)
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
    provider_name: str | None = None
    branch_name: str | None = None


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
    last_login_at: datetime | None = None    # latest successful sign-in recorded in the audit log


class Reason(Strict):
    reason: str


def _out(db: Session, s: Staff, viewer: Principal) -> StaffOut:
    rows = db.scalars(select(StaffRoleAssignment).where(StaffRoleAssignment.staff_id == s.staff_id)).all()
    # Names of the provider/branch an assignment is scoped to, only for providers the viewer may see: a teacher who also
    # works for another provider must not reveal that provider's name to a scoped admin.
    provider_ids = {r.provider_id for r in rows if r.provider_id and can_see_provider(viewer, r.provider_id)}
    branch_ids = {r.branch_id for r in rows if r.branch_id and r.provider_id in provider_ids}
    providers = dict(db.execute(select(Provider.provider_id, Provider.name).where(Provider.provider_id.in_(provider_ids))).all()) if provider_ids else {}
    branches = dict(db.execute(select(Branch.branch_id, Branch.name).where(Branch.branch_id.in_(branch_ids))).all()) if branch_ids else {}
    return StaffOut(
        staff_id=s.staff_id, staff_code=s.staff_code, full_name=s.full_name, email=s.email, phone=s.phone,
        status=s.status, email_verified=s.email_verified_at is not None,
        assignments=[AssignmentOut.model_validate(r).model_copy(update={
            "provider_name": providers.get(r.provider_id), "branch_name": branches.get(r.branch_id)}) for r in rows],
        created_at=s.created_at,
        last_login_at=db.scalar(select(func.max(AuditLog.occurred_at)).where(
            AuditLog.actor_staff_id == s.staff_id, AuditLog.action == "auth.login", AuditLog.outcome == "SUCCESS")),
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
    return _out(db, staff, p)


@router.get("/staff", response_model=Page[StaffOut])
def list_staff(q: str | None = Query(default=None, max_length=100), role: Role | None = None, status: str | None = Query(default=None, pattern="^(ACTIVE|INACTIVE)$"),
               provider_id: UUID | None = None, branch_id: UUID | None = None,
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
    # provider_id / branch_id only NARROW the result above (the scope filter for non-global callers still applies):
    # staff holding an active assignment there, plus teachers actively teaching in that branch.
    if provider_id:
        stmt = stmt.where(Staff.staff_id.in_(select(StaffRoleAssignment.staff_id).where(
            StaffRoleAssignment.provider_id == provider_id, StaffRoleAssignment.status == "ACTIVE")))
    if branch_id:
        stmt = stmt.where(or_(
            Staff.staff_id.in_(select(StaffRoleAssignment.staff_id).where(
                StaffRoleAssignment.branch_id == branch_id, StaffRoleAssignment.status == "ACTIVE")),
            Staff.staff_id.in_(select(TeacherSubjectAssignment.staff_id).where(
                TeacherSubjectAssignment.branch_id == branch_id, TeacherSubjectAssignment.status == "ACTIVE"))))
    res = paginate(db, stmt.order_by(Staff.staff_code), page, page_size)
    res["items"] = [_out(db, s, p) for s in res["items"]]
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
    return _out(db, s, p)


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
    return _out(db, s, p)


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
    if Role(a.role) == Role.SUPER_ADMIN and a.status == "ACTIVE":
        ensure_not_last_super_admin(db, s.staff_id)
    if not can_grant(p, Role(a.role), a.provider_id, a.branch_id):
        audit.record(db, "staff.end_assignment", "DENIED", actor=p.staff_id, target_type="staff", target_id=staff_id, ip=p.ip)
        raise api_error(403, "FORBIDDEN", "You may not modify this assignment.")
    a.status = "INACTIVE"
    # Removing a scope also retires the teaching assignments that depended on it.
    retire_teaching = a.role == Role.TEACHER.value
    db.commit()
    if retire_teaching:
        # The teaching assignments belong to the academics service. A teacher without an active TEACHER role gains no access from
        # leftover rows (authorization starts from the role), so a failed call is logged and retried by repeating the request.
        try:
            peer("academics").request("POST", "/teaching/retire", json={"staff_id": str(s.staff_id), "provider_id": str(a.provider_id)})
        except InternalServiceError:
            log.warning("could not retire teaching assignments of %s now; they stay inert without the role", s.staff_code)
    sessions.invalidate_staff_sessions(str(s.staff_id))
    audit.record(db, "staff.end_assignment", "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff_id,
                 provider_id=a.provider_id, branch_id=a.branch_id, reason=reason, ip=p.ip)
    return a


def ensure_not_last_super_admin(db: Session, target_id: UUID) -> None:
    """Refuse a change that would leave no ACTIVE Super Admin (an ACTIVE account holding an ACTIVE SUPER_ADMIN assignment). The other
    hierarchy rules (nobody acts on themselves; only a Super Admin acts on a Super Admin) already make this unreachable through the
    API today; this guard keeps it true regardless of how the rules evolve."""
    remaining = db.scalar(select(func.count(func.distinct(Staff.staff_id))).join(
        StaffRoleAssignment, StaffRoleAssignment.staff_id == Staff.staff_id).where(
        Staff.status == "ACTIVE", StaffRoleAssignment.role == Role.SUPER_ADMIN.value, StaffRoleAssignment.status == "ACTIVE",
        Staff.staff_id != target_id)) or 0
    if remaining == 0:
        raise api_error(409, "LAST_SUPER_ADMIN", "The platform must keep at least one active Super Admin.")


def _identity_status(db: Session, p: Principal, staff_id: UUID, status: str, reason: str, action: str) -> StaffOut:
    # Identity-wide changes affect every provider the person works for => global roles only.
    if not p.is_global:
        raise api_error(403, "FORBIDDEN", "Only platform staff can change a global identity's status.")
    reason = need_reason(reason)
    s = _get_staff(db, staff_id)
    if s.staff_id == p.staff_id:
        raise api_error(403, "FORBIDDEN", "You cannot change your own status.")
    _guard_target(db, p, s)
    if status == "INACTIVE" and Role.SUPER_ADMIN in _target_roles(db, s.staff_id):
        ensure_not_last_super_admin(db, s.staff_id)
    s.status = status
    s.deactivated_at = utcnow() if status == "INACTIVE" else None
    s.updated_by = p.staff_id
    db.commit()
    sessions.invalidate_staff_sessions(str(s.staff_id))
    audit.record(db, action, "SUCCESS", actor=p.staff_id, target_type="staff", target_id=staff_id, reason=reason, ip=p.ip)
    return _out(db, s, p)


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
