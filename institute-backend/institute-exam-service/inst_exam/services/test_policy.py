"""Shared policy rules for Daily, Weekly, and Global tests.

This module deliberately contains no database operations. Route handlers must
call these rules and separately validate ownership and enrollment in the DB.
Existing ExamPlan and exam-generation models remain the source of persistence.
"""

from enum import StrEnum


class TestType(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    GLOBAL = "GLOBAL"


class PolicyViolationError(ValueError):
    """Raised when a requested test operation violates policy."""


PLATFORM_ROLES = frozenset({"SUPER_ADMIN", "ADMIN"})
PROVIDER_ROLES = frozenset({"PROVIDER_ADMIN"})
BRANCH_ROLES = frozenset({"BRANCH_ADMIN"})
TEACHER_ROLES = frozenset({"TEACHER"})

ALL_ROLES = PLATFORM_ROLES | PROVIDER_ROLES | BRANCH_ROLES | TEACHER_ROLES


def normalize_role(role: str) -> str:
    """Normalize a role name without granting unknown roles permissions."""
    return str(role or "").strip().upper().replace(" ", "_").replace("-", "_")


def normalize_test_type(test_type: str | TestType) -> TestType:
    """Return a supported test type or fail closed."""
    try:
        return TestType(str(test_type).strip().upper())
    except (ValueError, AttributeError):
        raise PolicyViolationError(f"Unsupported test type: {test_type!r}") from None


def can_create_test(role: str, test_type: str | TestType) -> bool:
    """Only platform admins create Global Tests.

    Provider Admins create provider-scoped Daily/Weekly Tests.
    Branch Admins create branch-scoped Daily/Weekly Tests.
    Teachers cannot create tests.
    """
    normalized_role = normalize_role(role)
    kind = normalize_test_type(test_type)

    if normalized_role not in ALL_ROLES:
        return False

    if kind is TestType.GLOBAL:
        return normalized_role in PLATFORM_ROLES

    return normalized_role in PLATFORM_ROLES | PROVIDER_ROLES | BRANCH_ROLES


def can_upload_paper(
    role: str,
    test_type: str | TestType,
    *,
    has_explicit_test_access: bool,
    has_subject_contribution_permission: bool,
) -> bool:
    """Check the role-level gate for contributing a paper.

    This is not a replacement for database checks. The route must also verify
    test scope, provider/branch/batch ownership, subject, and student eligibility.
    """
    normalized_role = normalize_role(role)
    kind = normalize_test_type(test_type)

    if normalized_role not in ALL_ROLES:
        return False

    if not has_explicit_test_access or not has_subject_contribution_permission:
        return False

    if kind is TestType.GLOBAL:
        # Platform roles and explicitly authorized provider/branch staff may
        # contribute to a Global Test, including teachers.
        return normalized_role in ALL_ROLES

    # Provider-specific tests must be contributed to only by authorized staff.
    return normalized_role in (
        PLATFORM_ROLES | PROVIDER_ROLES | BRANCH_ROLES | TEACHER_ROLES
    )


def require_test_creation_permission(
    role: str,
    test_type: str | TestType,
) -> TestType:
    """Validate test creation and return its normalized type."""
    kind = normalize_test_type(test_type)
    if not can_create_test(role, kind):
        raise PolicyViolationError(
            f"Role {normalize_role(role)!r} cannot create {kind.value} tests."
        )
    return kind


def require_paper_upload_permission(
    role: str,
    test_type: str | TestType,
    *,
    has_explicit_test_access: bool,
    has_subject_contribution_permission: bool,
) -> TestType:
    """Validate paper contribution and return its normalized test type."""
    kind = normalize_test_type(test_type)
    if not can_upload_paper(
        role,
        kind,
        has_explicit_test_access=has_explicit_test_access,
        has_subject_contribution_permission=has_subject_contribution_permission,
    ):
        raise PolicyViolationError(
            f"Role {normalize_role(role)!r} cannot contribute a paper "
            f"to this {kind.value} test."
        )
    return kind
