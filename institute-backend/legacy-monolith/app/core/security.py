"""Authentication dependencies, CSRF/origin enforcement and default-deny authorization."""
import ipaddress
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import audit, sessions
from app.core.roles import GLOBAL_ROLES, ROLE_PERMISSIONS, Role
from app.database import get_db
from app.models.academics import TeacherSubjectAssignment
from app.models.staff import Staff, StaffRoleAssignment

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def cookie_name() -> str:
    # __Host- prefix pins the cookie to this exact host over HTTPS (no Domain attr).
    return "__Host-inst_sid" if settings.session_cookie_secure else "inst_sid"


def session_cookie_attributes() -> dict:
    """Attributes shared by setting AND clearing the session cookie. Browsers ignore a deletion whose attributes do not
    satisfy the cookie's requirements: a __Host- cookie can only be cleared with Secure and Path=/ (and no Domain)."""
    return {"httponly": True, "secure": settings.session_cookie_secure,
            "samesite": settings.session_cookie_samesite.lower(), "path": "/"}  # no Domain attribute => host-only


def set_session_cookie(response, sid: str, max_age: int) -> None:
    response.set_cookie(key=cookie_name(), value=sid, max_age=max_age, **session_cookie_attributes())


def clear_session_cookie(response) -> None:
    response.delete_cookie(cookie_name(), **session_cookie_attributes())


def client_ip(request: Request) -> str:
    """The peer address if it is a valid IP, else "unknown" (never an arbitrary string: it is
    stored in INET columns and used as a rate-limit key)."""
    host = request.client.host if request.client else ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return "unknown"


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def origin_check(request: Request) -> None:
    """Reject browser requests from origins outside the allowlist (state-changing only)."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in settings.origins:
        raise api_error(403, "ORIGIN_NOT_ALLOWED", "Request origin is not allowed.")


@dataclass
class Principal:
    staff: Staff
    assignments: list[StaffRoleAssignment]
    teaching: list[TeacherSubjectAssignment]
    session_id: str
    csrf: str
    ip: str = "unknown"
    _roles: set[Role] = field(default_factory=set)

    def __post_init__(self):
        self._roles = {Role(a.role) for a in self.assignments}

    @property
    def staff_id(self) -> UUID:
        return self.staff.staff_id

    @property
    def roles(self) -> set[Role]:
        return self._roles

    def has_role(self, *roles: Role) -> bool:
        return bool(self._roles & set(roles))

    @property
    def is_global(self) -> bool:
        return bool(self._roles & GLOBAL_ROLES)

    # ------------------------------------------------------------ authorization
    def can(
        self,
        permission: str,
        provider_id: UUID | None = None,
        branch_id: UUID | None = None,
        subject_id: UUID | None = None,
        batch_id: UUID | None = None,
        exclude_roles: tuple[Role, ...] = (),
    ) -> bool:
        """Default deny. True only if some ACTIVE assignment grants `permission`
        for the requested scope. Scope arguments left as None are 'not
        constrained by the request' (list endpoints must additionally filter)."""
        for a in self.assignments:
            role = Role(a.role)
            if role in exclude_roles or permission not in ROLE_PERMISSIONS[role]:
                continue
            if role in GLOBAL_ROLES:
                return True
            if provider_id is None or a.provider_id != provider_id:
                continue
            if role == Role.PROVIDER_ADMIN:
                return True
            if role == Role.BRANCH_ADMIN:
                if branch_id is not None and a.branch_id == branch_id:
                    return True
                continue
            if role == Role.TEACHER:
                if branch_id is None and subject_id is None and batch_id is None:
                    return True  # provider-level read; endpoints then filter by teaching
                if self._teaches(provider_id, branch_id, subject_id, batch_id):
                    return True
        return False

    def _teaches(self, provider_id, branch_id, subject_id, batch_id) -> bool:
        for t in self.teaching:
            if t.provider_id != provider_id:
                continue
            if branch_id is not None and t.branch_id != branch_id:
                continue
            if subject_id is not None and t.subject_id != subject_id:
                continue
            if batch_id is not None and t.batch_id is not None and t.batch_id != batch_id:
                continue
            return True
        return False

    def require(self, db: Session, permission: str, **scope) -> None:
        if not self.can(permission, **scope):
            audit.record(
                db, "authz.denied", "DENIED", actor=self.staff_id, ip=self.ip,
                provider_id=scope.get("provider_id"), branch_id=scope.get("branch_id"),
                details={"permission": permission},
            )
            raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")

    def scope_provider_ids(self, permission: str) -> set[UUID] | None:
        """Providers visible for `permission`; None means unrestricted (global role)."""
        ids: set[UUID] = set()
        for a in self.assignments:
            role = Role(a.role)
            if permission not in ROLE_PERMISSIONS[role]:
                continue
            if role in GLOBAL_ROLES:
                return None
            ids.add(a.provider_id)
        return ids

    def scope_branch_ids(self, permission: str, provider_id: UUID) -> set[UUID] | None:
        """Branches of `provider_id` visible for `permission`; None = all of them."""
        out: set[UUID] = set()
        for a in self.assignments:
            role = Role(a.role)
            if permission not in ROLE_PERMISSIONS[role]:
                continue
            if role in GLOBAL_ROLES:
                return None
            if a.provider_id != provider_id:
                continue
            if role == Role.PROVIDER_ADMIN:
                return None
            if role == Role.BRANCH_ADMIN:
                out.add(a.branch_id)
            elif role == Role.TEACHER:
                out.update(t.branch_id for t in self.teaching if t.provider_id == provider_id)
        return out

    def has_perm(self, permission: str) -> bool:
        """True if any active role grants the permission, ignoring scope (callers must scope)."""
        return any(permission in ROLE_PERMISSIONS[r] for r in self._roles)

    def highest_role(self) -> Role:
        from app.core.roles import RANK
        return max(self._roles, key=lambda r: RANK[r])


def load_assignments(db: Session, staff_id: UUID) -> list[StaffRoleAssignment]:
    return list(
        db.scalars(
            select(StaffRoleAssignment).where(
                StaffRoleAssignment.staff_id == staff_id, StaffRoleAssignment.status == "ACTIVE"
            )
        )
    )


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    unauth = api_error(401, "NOT_AUTHENTICATED", "Not authenticated.")
    origin_check(request)
    sid = request.cookies.get(cookie_name())
    if not sid:
        raise unauth
    data = sessions.get_session(sid)
    if not data:
        raise unauth
    staff = db.get(Staff, UUID(data["staff_id"]))
    if not staff or staff.status != "ACTIVE":
        sessions.delete_session(sid)
        raise unauth
    if request.method not in SAFE_METHODS:
        import hmac
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(supplied, data["csrf"]):
            raise api_error(403, "CSRF_FAILED", "Missing or invalid CSRF token.")
    assignments = load_assignments(db, staff.staff_id)
    teaching = list(
        db.scalars(
            select(TeacherSubjectAssignment).where(
                TeacherSubjectAssignment.staff_id == staff.staff_id,
                TeacherSubjectAssignment.status == "ACTIVE",
            )
        )
    )
    return Principal(staff, assignments, teaching, sid, data["csrf"], client_ip(request))


def require_any_role(principal: Principal = Depends(get_principal)) -> Principal:
    """Authenticated staff with at least one ACTIVE assignment (default deny otherwise)."""
    if not principal.assignments:
        raise api_error(403, "NO_ACTIVE_ROLE", "No active role assignment.")
    return principal


# ---- helpers shared by route modules -------------------------------------------------

def can_see_provider(p: Principal, provider_id: UUID) -> bool:
    return p.is_global or any(a.provider_id == provider_id for a in p.assignments)


def can_grant(p: Principal, role: Role, provider_id: UUID | None, branch_id: UUID | None) -> bool:
    """May `p` create/modify/deactivate a `role` assignment in this scope? Default deny."""
    from app.core.roles import MANAGEABLE_ROLES, scope_is_valid
    if not scope_is_valid(role, provider_id, branch_id):
        return False
    for a in p.assignments:
        ar = Role(a.role)
        if "staff.assign_role" not in ROLE_PERMISSIONS[ar] or role not in MANAGEABLE_ROLES[ar]:
            continue
        if ar in GLOBAL_ROLES:
            return True
        if a.provider_id != provider_id:
            continue
        if ar == Role.PROVIDER_ADMIN:
            return True
        if ar == Role.BRANCH_ADMIN and role == Role.TEACHER:
            return True  # teacher access is still bounded by teaching assignments
    return False
