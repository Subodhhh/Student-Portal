import pytest

from inst_exam.services.test_policy import (
    PolicyViolationError,
    can_create_test,
    can_upload_paper,
    normalize_test_type,
    require_paper_upload_permission,
    require_test_creation_permission,
)


@pytest.mark.parametrize("role", ["SUPER_ADMIN", "ADMIN"])
def test_platform_admin_can_create_all_test_types(role):
    for test_type in ("DAILY", "WEEKLY", "GLOBAL"):
        assert can_create_test(role, test_type)


@pytest.mark.parametrize("role", ["PROVIDER_ADMIN", "BRANCH_ADMIN"])
def test_provider_and_branch_admin_cannot_create_global_tests(role):
    assert can_create_test(role, "DAILY")
    assert can_create_test(role, "WEEKLY")
    assert not can_create_test(role, "GLOBAL")


def test_teacher_cannot_create_tests():
    for test_type in ("DAILY", "WEEKLY", "GLOBAL"):
        assert not can_create_test("TEACHER", test_type)


def test_unknown_role_fails_closed():
    assert not can_create_test("UNKNOWN_ROLE", "DAILY")
    assert not can_upload_paper(
        "UNKNOWN_ROLE",
        "GLOBAL",
        has_explicit_test_access=True,
        has_subject_contribution_permission=True,
    )


def test_global_paper_upload_requires_explicit_access_and_subject_permission():
    assert can_upload_paper(
        "TEACHER",
        "GLOBAL",
        has_explicit_test_access=True,
        has_subject_contribution_permission=True,
    )
    assert not can_upload_paper(
        "TEACHER",
        "GLOBAL",
        has_explicit_test_access=False,
        has_subject_contribution_permission=True,
    )
    assert not can_upload_paper(
        "TEACHER",
        "GLOBAL",
        has_explicit_test_access=True,
        has_subject_contribution_permission=False,
    )


def test_invalid_test_type_is_rejected():
    with pytest.raises(PolicyViolationError):
        normalize_test_type("COMPOSITE")


def test_permission_helpers_return_normalized_type():
    assert require_test_creation_permission("PROVIDER_ADMIN", "daily").value == "DAILY"
    assert require_paper_upload_permission(
        "TEACHER",
        "global",
        has_explicit_test_access=True,
        has_subject_contribution_permission=True,
    ).value == "GLOBAL"


def test_teacher_creation_is_rejected():
    with pytest.raises(PolicyViolationError):
        require_test_creation_permission("TEACHER", "DAILY")
