"""The five institute roles and their permissions (single source of truth).

Scope semantics
  SUPER_ADMIN, ADMIN  platform-wide (no provider/branch)
  PROVIDER_ADMIN      one provider (all of its branches)
  BRANCH_ADMIN        one provider + one branch
  TEACHER             one provider; reaches branch/subject/batch data only
                      through explicit teaching assignments
"""
from enum import Enum


class Role(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    PROVIDER_ADMIN = "PROVIDER_ADMIN"
    BRANCH_ADMIN = "BRANCH_ADMIN"
    TEACHER = "TEACHER"


GLOBAL_ROLES = {Role.SUPER_ADMIN, Role.ADMIN}
MFA_ROLES = {Role.SUPER_ADMIN, Role.ADMIN, Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN}
# Higher number = more authority; used for dashboard selection and anti-escalation.
RANK = {Role.SUPER_ADMIN: 5, Role.ADMIN: 4, Role.PROVIDER_ADMIN: 3, Role.BRANCH_ADMIN: 2, Role.TEACHER: 1}

# Roles an actor may grant/modify. Default deny: absent = nothing.
MANAGEABLE_ROLES = {
    Role.SUPER_ADMIN: {Role.SUPER_ADMIN, Role.ADMIN, Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN, Role.TEACHER},
    Role.ADMIN: {Role.ADMIN, Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN, Role.TEACHER},
    Role.PROVIDER_ADMIN: {Role.BRANCH_ADMIN, Role.TEACHER},
    Role.BRANCH_ADMIN: {Role.TEACHER},
    Role.TEACHER: set(),
}


def scope_is_valid(role: Role, provider_id, branch_id) -> bool:
    if role in GLOBAL_ROLES:
        return provider_id is None and branch_id is None
    if role == Role.PROVIDER_ADMIN:
        return provider_id is not None and branch_id is None
    if role == Role.BRANCH_ADMIN:
        return provider_id is not None and branch_id is not None
    return provider_id is not None and branch_id is None  # TEACHER


_ALL = {
    "provider.create", "provider.read", "provider.update", "provider.deactivate",
    "branch.create", "branch.read", "branch.update", "branch.deactivate",
    "staff.create", "staff.read", "staff.update", "staff.deactivate", "staff.assign_role",
    "student.read", "student.create", "student.link",
    "subject.manage", "batch.manage", "enrollment.manage",
    "enrollment.transfer_request", "enrollment.transfer_approve", "teaching.assign",
    "question.read", "question.manage",
    "paper.upload", "paper.create", "paper.approve", "paper.publish", "paper.read",
    "paper.override_status",
    "examplan.manage", "exam.read", "exam.generate", "exam.schedule", "exam.publish",
    "result.read", "result.correct", "result.correct.own",
    "analytics.read", "notification.manage", "notification.read",
    "support.manage", "billing.read", "billing.routine", "billing.refund", "billing.adjust",
    "settings.platform", "settings.provider", "settings.branch", "audit.read",
}

ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.SUPER_ADMIN: set(_ALL),
    Role.ADMIN: _ALL - {
        "paper.override_status", "result.correct", "result.correct.own", "billing.refund", "billing.adjust",
        "settings.platform",
    },
    Role.PROVIDER_ADMIN: {
        "provider.read", "provider.update",
        "branch.create", "branch.read", "branch.update", "branch.deactivate",
        "staff.create", "staff.read", "staff.update", "staff.deactivate", "staff.assign_role",
        "student.read", "student.create", "student.link",
        "subject.manage", "batch.manage", "enrollment.manage",
        "enrollment.transfer_request", "enrollment.transfer_approve", "teaching.assign",
        "question.read", "question.manage",
        "paper.upload", "paper.create", "paper.approve", "paper.publish", "paper.read",
        "examplan.manage", "exam.read", "exam.generate", "exam.schedule", "exam.publish",
        "result.read", "result.correct.own", "analytics.read", "notification.manage", "notification.read",
        "support.manage", "billing.read", "settings.provider", "settings.branch", "audit.read",
    },
    Role.BRANCH_ADMIN: {
        "provider.read", "branch.read", "branch.update",
        "staff.create", "staff.read", "staff.update", "staff.deactivate", "staff.assign_role",
        "student.read", "student.create", "student.link",
        "subject.manage", "batch.manage", "enrollment.manage", "enrollment.transfer_request",
        "teaching.assign",
        "question.read", "question.manage",
        "paper.upload", "paper.create", "paper.approve", "paper.publish", "paper.read",
        "exam.read", "exam.generate", "exam.schedule", "exam.publish",
        "result.read", "result.correct.own", "analytics.read", "notification.manage", "notification.read",
        "support.manage", "settings.branch",
    },
    Role.TEACHER: {
        "provider.read", "branch.read", "student.read",
        "question.read", "question.manage",
        "paper.upload", "paper.create", "paper.approve", "paper.publish", "paper.read",
        "exam.read", "result.read", "result.correct.own", "analytics.read", "notification.read",
    },
}
