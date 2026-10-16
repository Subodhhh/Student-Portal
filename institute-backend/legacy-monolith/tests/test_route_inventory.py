"""Systematic checks driven by the OpenAPI schema (not by a hand-kept endpoint list)."""
import re
from uuid import uuid4

import pytest

from app.core.roles import Role
from app.main import app
from tests.conftest import ORIGIN

# Intentionally public (pre-authentication or infrastructure) operations.
PUBLIC = {
    ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/mfa/verify"), ("POST", "/api/v1/auth/mfa/resend"),
    ("POST", "/api/v1/auth/verify-email"), ("POST", "/api/v1/auth/forgot-password"), ("POST", "/api/v1/auth/reset-password"),
    ("GET", "/health"), ("GET", "/ready"),
}


def operations():
    spec = app.openapi()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            yield method.upper(), path, op


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda m: "STU000001" if m.group(0) == "{enrollment_id}" else str(uuid4()), path)


def required_query(op) -> dict:
    out = {}
    for p in op.get("parameters", []):
        if p["in"] == "query" and p.get("required"):
            out[p["name"]] = "a@example.com" if "email" in p["name"] else str(uuid4())
    return out


def test_inventory_is_not_empty():
    assert len(list(operations())) > 90


def test_every_non_public_route_rejects_anonymous_callers(client):
    unprotected = []
    for method, path, op in operations():
        if (method, path) in PUBLIC:
            continue
        r = client.request(method, fill(path), params=required_query(op), json={} if method != "GET" else None, headers=ORIGIN)
        if r.status_code != 401:
            unprotected.append((method, path, r.status_code))
    assert unprotected == []


def test_student_session_cookie_never_authenticates_anything(client):
    client.cookies.set("session_id", "student-session-value")
    client.cookies.set("student_session", "x")
    bad = [(m, p) for m, p, op in operations() if (m, p) not in PUBLIC
           and client.request(m, fill(p), params=required_query(op), json={} if m != "GET" else None, headers=ORIGIN).status_code != 401]
    assert bad == []


def test_public_operations_are_exactly_the_documented_set():
    documented = {(m, p) for m, p, _ in operations() if p.startswith("/api/v1/auth/") or p in {"/health", "/ready"}}
    stateful_auth = {(m, p) for m, p in documented if p not in {"/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/change-password"}}
    assert PUBLIC == stateful_auth


@pytest.mark.parametrize("role,scope", [
    (Role.SUPER_ADMIN, (None, None)), (Role.ADMIN, (None, None)), (Role.PROVIDER_ADMIN, ("p1", None)),
    (Role.BRANCH_ADMIN, ("p1", "p1b1")), (Role.TEACHER, ("p1", None)),
])
def test_no_role_can_trigger_a_server_error_on_any_route(client, world, make_staff, login, role, scope):
    """Fuzz every operation with random ids / empty bodies as each role: only well-formed 4xx (or 2xx)
    answers are acceptable; a 500 would mean an unhandled path an attacker could probe."""
    _, code = make_staff([(role, world.get(scope[0]), world.get(scope[1]))])
    h = login(code)
    errors = []
    for method, path, op in operations():
        if path.startswith("/api/v1/auth/") or path in {"/health", "/ready"}:
            continue
        if "multipart/form-data" in str(op.get("requestBody", {})):
            continue
        r = client.request(method, fill(path), params=required_query(op), json={} if method != "GET" else None, headers=h)
        if r.status_code >= 500 and r.status_code != 503:   # 503 = explicit "dependency unavailable"
            errors.append((method, path, r.status_code))
    assert errors == []


def test_cross_tenant_probe_matrix_never_leaks(client, world, make_staff, login, db_factory):
    """A Provider Admin of p2 hits every GET with p1's real object ids: nothing from p1 may come back."""
    from app.models.org import Provider  # noqa: F401
    ids = {"provider_id": world["p1"], "branch_id": world["p1b1"]}
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p2"], None)])
    h = login(code)
    leaks = []
    for method, path, op in operations():
        if method != "GET" or path.startswith("/api/v1/auth/") or path in {"/health", "/ready"} or "{" not in path:
            continue
        url = re.sub(r"\{provider_id\}", str(ids["provider_id"]), path)
        url = re.sub(r"\{branch_id\}", str(ids["branch_id"]), url)
        url = fill(url)
        r = client.get(url, params=required_query(op), headers=h)
        if r.status_code == 200 and (str(world["p1"]) in r.text or str(world["p1b1"]) in r.text or "Provider p1" in r.text):
            leaks.append((path, r.status_code))
    assert leaks == []
