"""Who may see and who may change Question Bank content: the one home of these rules (question routes, paper building, taxonomy
reads, pictures and the bulk import API and worker all use them). Seeing and changing are deliberately separate.

Three scopes, one row per question (docs/institute-portal/THREE_SCOPE_QUESTION_BANK_PLAN.md):
  * GLOBAL (platform): no provider, no branch. Every staff member holding question.read sees it; only platform roles change it.
  * PROVIDER with branch_id NULL: provider-wide, created by a Provider Admin.
  * PROVIDER with branch_id set: uploaded by that branch (its ORIGIN).
Every PROVIDER question is shared with ALL branches of its provider - decided here by provider_id, nothing is copied per branch:
  * seeing it: platform roles; Provider Admins and Branch Admins of that provider; teachers of that provider, for the subjects they
    teach (in any of its branches);
  * changing it (edit, new version, archive): platform roles and the Provider Admin; for a branch question also the Branch Admin of
    the ORIGIN branch and the teacher who owns it. Other branches use it, they do not change it.
Staff of another provider never see or change it."""
from uuid import UUID

from sqlalchemy import and_, false
from sqlalchemy.orm import Session

from institute_common.core import audit
from institute_common.core.roles import GLOBAL_ROLES, ROLE_PERMISSIONS, Role
from institute_common.core.security import Principal, api_error
from institute_common.models.papers import Question


def readable_subjects(p: Principal, provider_id: UUID, permission: str = "question.read") -> set[UUID] | None:
    """None: every subject of the provider's bank; otherwise the subjects (possibly none) whose PROVIDER questions `p` may see."""
    subjects: set[UUID] = set()
    for a in p.assignments:
        role = Role(a.role)
        if permission not in ROLE_PERMISSIONS[role]:
            continue
        if role in GLOBAL_ROLES:
            return None
        if a.provider_id != provider_id:
            continue
        if role in (Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN):
            return None
        if role == Role.TEACHER:
            subjects.update(t.subject_id for t in p.teaching if t.provider_id == provider_id)
    return subjects


def deny(db: Session, p: Principal, permission: str, provider_id: UUID | None, branch_id: UUID | None = None, **details) -> None:
    audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=provider_id, branch_id=branch_id,
                 details={"permission": permission, **details})
    raise api_error(403, "FORBIDDEN", "You do not have permission for this action.")


def require_provider_read(db: Session, p: Principal, provider_id: UUID, subject_id: UUID | None = None,
                          permission: str = "question.read") -> set[UUID] | None:
    """readable_subjects, refused (403, audited) when `p` may see nothing of this provider's bank (or not `subject_id`)."""
    subjects = readable_subjects(p, provider_id, permission)
    if subjects is not None and (not subjects or (subject_id is not None and subject_id not in subjects)):
        deny(db, p, permission, provider_id)
    return subjects


def provider_condition(provider_id: UUID, subjects: set[UUID] | None):
    """SQL: the PROVIDER questions of one provider (all its branches and provider-wide), limited to `subjects` unless None."""
    cond = and_(Question.scope == "PROVIDER", Question.provider_id == provider_id)
    if subjects is None:
        return cond
    return and_(cond, Question.subject_id.in_(subjects)) if subjects else false()


def can_read_question(p: Principal, q: Question) -> bool:
    if q.scope == "GLOBAL":
        return p.has_perm("question.read")
    subjects = readable_subjects(p, q.provider_id)
    return subjects is None or q.subject_id in subjects


def can_manage_question(p: Principal, q: Question) -> bool:
    """May `p` edit, version or archive `q`? Platform roles and the Provider Admin; for a branch question also the Branch Admin of its
    origin branch and its owning teacher (Principal.can: a Branch Admin only matches its own branch, so a provider-wide question
    (branch NULL) is the Provider Admin's)."""
    if q.scope == "GLOBAL":
        return p.is_global and p.has_perm("question.manage")
    kw = dict(provider_id=q.provider_id, branch_id=q.branch_id, subject_id=q.subject_id)
    return p.can("question.manage", exclude_roles=(Role.TEACHER,), **kw) or (p.can("question.manage", **kw) and q.owner_staff_id == p.staff_id)


def require_read_question(db: Session, p: Principal, q: Question) -> None:
    if not can_read_question(p, q):
        deny(db, p, "question.read", q.provider_id, question_id=str(q.question_id))


def require_manage_question(db: Session, p: Principal, q: Question) -> None:
    if can_manage_question(p, q):
        return
    if q.scope == "GLOBAL":
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, details={"permission": "question.manage", "reason": "global_content"})
        raise api_error(403, "FORBIDDEN", "Only platform administrators may change platform questions and papers.")
    if can_read_question(p, q):
        # visible (shared with the caller's branch) but owned by another branch, the provider, or another teacher
        audit.record(db, "authz.denied", "DENIED", actor=p.staff_id, ip=p.ip, provider_id=q.provider_id, branch_id=q.branch_id,
                     details={"permission": "question.manage", "reason": "not_owner", "question_id": str(q.question_id)})
        raise api_error(403, "FORBIDDEN", "This question belongs to another branch or to the provider: you may use it, not change it.")
    deny(db, p, "question.manage", q.provider_id, q.branch_id, question_id=str(q.question_id))


def is_provider_admin(p: Principal, provider_id: UUID, permission: str = "question.manage") -> bool:
    """Provider-wide questions are created (one by one or in bulk) by the provider's own Provider Admin only - not by platform roles,
    Branch Admins or teachers (decisions D1 and D3)."""
    return any(Role(a.role) == Role.PROVIDER_ADMIN and a.provider_id == provider_id and permission in ROLE_PERMISSIONS[Role.PROVIDER_ADMIN]
               for a in p.assignments)


def import_subjects(p: Principal, provider_id: UUID) -> set[UUID] | None:
    """Bulk Upload goes to the provider's central (provider-wide) bank for everyone who may add questions somewhere in the provider
    (owner decisions 2026-11-22): platform administrators holding question.manage (any provider), its Provider Admin and Branch Admins
    (every subject; None = no restriction), a teacher (the subjects they teach there). An empty set: may not bulk upload to it."""
    if p.is_global:
        return None if p.has_perm("question.manage") else set()
    roles = {Role(a.role) for a in p.assignments if a.provider_id == provider_id and "question.manage" in ROLE_PERMISSIONS[Role(a.role)]}
    if roles & {Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN}:
        return None
    return {t.subject_id for t in p.teaching if t.provider_id == provider_id} if Role.TEACHER in roles else set()


def may_import_provider_wide(p: Principal, provider_id: UUID, subject_id: UUID) -> bool:
    subjects = import_subjects(p, provider_id)
    return subjects is None or subject_id in subjects


def single_provider(p: Principal, permission: str = "question.read") -> UUID | None:
    """The caller's provider when it is unambiguous (non-platform staff of exactly one provider); None otherwise."""
    if p.is_global:
        return None
    providers = {a.provider_id for a in p.assignments if permission in ROLE_PERMISSIONS[Role(a.role)] and a.provider_id}
    return next(iter(providers)) if len(providers) == 1 else None
