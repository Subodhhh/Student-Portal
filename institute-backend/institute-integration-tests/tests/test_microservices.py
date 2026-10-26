"""Service boundaries: ownership, internal APIs, audit delivery, gateway routing and parity with the legacy monolith."""
import importlib
import os
import sys
import uuid

import pytest

from institute_common.context import service_context
from institute_common.database import utcnow
from institute_common.ownership import OWNED, OwnershipViolation, attach_guard, owner_of
from institute_common.core.roles import Role
from tests.conftest import ORIGIN, PASSWORD  # noqa: F401
from tests.gateway import SERVICES, TABLE

API = "/api/v1"
INTERNAL = {"X-Service-Key": "test-internal-key"}


# ------------------------------------------------------------------ ownership

def test_every_mapped_table_has_exactly_one_owner():
    from institute_common.database import Base
    import institute_common.models  # noqa: F401
    owners = {}
    for service, tables in OWNED.items():
        for t in tables:
            assert t not in owners, f"{t} owned by {owners[t]} and {service}"
            owners[t] = service
    mapped = set(Base.metadata.tables)
    spool = {t for t in mapped if t.endswith("_audit_spool")}
    # `students` is owned by the Student identity service; everything else mapped here must be claimed by one Institute service
    assert mapped - set(owners) - spool - {"students"} == set()
    assert {owner_of(t) for t in spool} == {"organization", "academics", "enrollment", "paper", "exam", "operations"}


def test_a_service_cannot_write_tables_it_does_not_own(db_factory):
    from institute_common.models.org import Provider
    from institute_common.models.exams import ExamPlan  # noqa: F401
    for service, bad_table_owner in (("academics", "organization"), ("exam", "organization"), ("operations", "organization")):
        with service_context(service), db_factory() as db:
            attach_guard(db)
            db.add(Provider(name="Sneaky", email=f"{uuid.uuid4().hex}@example.com"))
            with pytest.raises(OwnershipViolation, match=bad_table_owner):
                db.flush()
    with service_context("organization"), db_factory() as db:                 # the owner may
        attach_guard(db)
        db.add(Provider(name="Legit", email=f"{uuid.uuid4().hex}@example.com"))
        db.commit()


def test_reading_foreign_tables_is_still_possible_but_updating_them_is_not(db_factory, world):
    from institute_common.models.org import Provider
    with service_context("exam"), db_factory() as db:
        attach_guard(db)
        p = db.get(Provider, world["p1"])
        assert p is not None
        p.name = "renamed by the wrong service"
        with pytest.raises(OwnershipViolation):
            db.flush()


# ------------------------------------------------------------------ internal APIs

def test_internal_apis_are_hidden_without_a_key_and_reject_wrong_keys(client, monkeypatch):
    from institute_common.config import settings
    from tests.gateway import identity
    from fastapi.testclient import TestClient
    direct = TestClient(identity)
    assert direct.post("/internal/v1/introspect", json={"session_id": "x" * 12, "method": "GET"}).status_code == 401
    assert direct.post("/internal/v1/introspect", json={"session_id": "x" * 12, "method": "GET"}, headers={"X-Service-Key": "wrong"}).status_code == 401
    monkeypatch.setattr(settings, "internal_api_keys", "")
    assert direct.post("/internal/v1/introspect", json={"session_id": "x" * 12, "method": "GET"}, headers=INTERNAL).status_code == 404   # the API does not exist


def test_internal_paths_are_not_reachable_through_the_gateway(client):
    for path in ("/internal/v1/introspect", "/internal/v1/audit/events", "/internal/v1/notifications", "/internal/v1/teaching/retire", "/internal/v1/paper-shares"):
        assert client.post(path, json={}, headers=INTERNAL).status_code == 404


def test_introspection_reports_identity_roles_and_rejects_bad_sessions(client, world, make_staff, login):
    from fastapi.testclient import TestClient
    from tests.gateway import identity
    _, code = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])
    h = login(code)
    sid = client.cookies.get("inst_sid")
    direct = TestClient(identity)
    ok = direct.post("/internal/v1/introspect", json={"session_id": sid, "method": "GET"}, headers=INTERNAL)
    assert ok.status_code == 200 and ok.json()["assignments"][0]["role"] == "PROVIDER_ADMIN"
    assert direct.post("/internal/v1/introspect", json={"session_id": sid, "method": "POST", "csrf_token": "nope"}, headers=INTERNAL).status_code == 403
    assert direct.post("/internal/v1/introspect", json={"session_id": sid, "method": "POST", "csrf_token": h["X-CSRF-Token"]}, headers=INTERNAL).status_code == 200
    assert direct.post("/internal/v1/introspect", json={"session_id": "s" * 40, "method": "GET"}, headers=INTERNAL).status_code == 401


def test_other_services_depend_on_identity_for_authentication(client, world, make_staff, login):
    """Every public route of every non-identity service authenticates through the identity service, not by itself."""
    _, code = make_staff([(Role.SUPER_ADMIN, None, None)])
    login(code)
    for service, path in (("organization", "/providers"), ("academics", "/subjects"), ("operations", "/notifications"), ("paper", "/branches/" + str(world["p1b1"]) + "/papers")):
        assert client.get(API + path).status_code in (200, 404), service
    client.cookies.clear()
    for path in ("/providers", "/subjects", "/notifications"):
        assert client.get(API + path).status_code == 401


def test_identity_outage_fails_closed_with_503(client, world, make_staff, login, monkeypatch):
    from institute_common.internal_http import InternalServiceError
    from institute_common.core import security
    _, code = make_staff([(Role.SUPER_ADMIN, None, None)])
    login(code)

    class Down:
        def request(self, *a, **k):
            raise InternalServiceError("down")

    monkeypatch.setattr(security, "current_service", lambda: "organization")
    import institute_common.internal_http as ih
    monkeypatch.setattr(ih, "peer", lambda *a, **k: Down())
    r = client.get(f"{API}/providers")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "IDENTITY_UNAVAILABLE"


# ------------------------------------------------------------------ audit delivery

def test_audit_events_of_other_services_reach_the_identity_log_once(client, world, make_staff, login, db_factory, monkeypatch):
    from institute_common.config import settings
    from institute_common.models.audit_spool import SPOOLS
    from institute_common.models.staff import AuditLog
    monkeypatch.setattr(settings, "audit_forward_inline", False)
    _, code = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(code)
    r = client.post(f"{API}/providers", json={"name": "Audit Probe", "email": "probe@example.com"}, headers=h)
    assert r.status_code == 201
    with db_factory() as db:
        assert db.query(SPOOLS["organization"]).count() >= 1
        assert db.query(AuditLog).filter_by(action="provider.create").count() == 0     # not delivered yet
        from institute_common.audit_forward import forward_spool
        from tests.gateway import identity
        from fastapi.testclient import TestClient
        direct = TestClient(identity)
        from institute_common.internal_http import InternalHttp
        http = InternalHttp("", "test-internal-key", client=direct)
        assert forward_spool(db, "organization", http)["sent"] >= 1
        assert db.query(AuditLog).filter_by(action="provider.create").count() == 1
        row = db.query(AuditLog).filter_by(action="provider.create").one()
        assert row.actor_service == "institute-organization" or row.actor_staff_id is not None
        again = db.query(SPOOLS["organization"]).all()
        assert all(x.forwarded_at is not None for x in again)
        # replaying the same events inserts nothing
        from institute_common.audit_forward import ingest_events
        events = [{"event_id": str(x.event_id), **x.payload} for x in again]
        assert ingest_events(db, "organization", events) == 0


def test_audit_spool_keeps_events_when_identity_is_down(db_factory, monkeypatch):
    from institute_common.audit_forward import forward_spool
    from institute_common.core import audit
    from institute_common.internal_http import InternalServiceError
    from institute_common.models.audit_spool import SPOOLS
    from institute_common.config import settings
    monkeypatch.setattr(settings, "audit_forward_inline", False)
    with service_context("paper"), db_factory() as db:
        audit.record(db, "paper.probe", "SUCCESS", details={"password": "must-not-leak"})
        row = db.query(SPOOLS["paper"]).one()
        assert row.payload["details"]["password"] == "[redacted]"

        class Down:
            def request(self, *a, **k):
                raise InternalServiceError("identity down")

        assert forward_spool(db, "paper", Down()) == {"sent": 0, "pending": 1}
        db.refresh(row)
        assert row.forwarded_at is None and row.attempts == 1


def test_ingestion_rejects_unknown_senders_and_malformed_events(client, db_factory):
    from fastapi.testclient import TestClient
    from tests.gateway import identity
    direct = TestClient(identity)
    assert direct.post("/internal/v1/audit/events", json={"service": "identity", "events": []}, headers=INTERNAL).status_code == 422
    assert direct.post("/internal/v1/audit/events", json={"service": "paper", "events": [{"nope": 1}]}, headers=INTERNAL).status_code == 422
    assert direct.post("/internal/v1/audit/events", json={"service": "paper", "events": []}, headers=INTERNAL).json() == {"received": 0, "inserted": 0}


# ------------------------------------------------------------------ cross-service writes go through APIs

def test_ending_a_teacher_role_retires_teaching_through_the_academics_service(client, world, make_staff, login, db_factory):
    from institute_common.models.academics import Subject, ProviderSubject, TeacherSubjectAssignment
    t, tcode = make_staff([(Role.TEACHER, world["p1"], None)])
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    with db_factory() as db:
        s = Subject(code="PHY", name="Physics")
        db.add(s)
        db.flush()
        db.add(ProviderSubject(provider_id=world["p1"], subject_id=s.subject_id))
        db.commit()
        sid = s.subject_id
    h = login(sa)
    assert client.post(f"{API}/teaching", json={"staff_id": str(t), "branch_id": str(world["p1b1"]), "subject_id": str(sid)}, headers=h).status_code == 201
    a = client.get(f"{API}/staff/{t}", headers=h).json()["assignments"][0]["assignment_id"]
    assert client.request("DELETE", f"{API}/staff/{t}/assignments/{a}", json={"reason": "moved to another role"}, headers=h).status_code == 200
    with db_factory() as db:
        assert db.query(TeacherSubjectAssignment).filter_by(staff_id=t).one().status == "INACTIVE"


def test_exam_notifications_are_delivered_to_operations_and_retried_when_it_is_down(client, db_factory, world, make_staff):
    from institute_common.internal_http import InternalServiceError
    from institute_common.models.exams import ExamNotificationOutbox
    from institute_common.models.exams import Notification
    from inst_exam.services import notify as exam_notify
    sid, _ = make_staff([(Role.PROVIDER_ADMIN, world["p1"], None)])

    class Down:
        def request(self, *a, **k):
            raise InternalServiceError("operations down")

    from institute_common import internal_http
    real = internal_http._PEERS.pop("operations")
    try:
        with service_context("exam"), db_factory() as db:
            assert exam_notify.notify(db, [sid], kind="EXAM_PUBLISH_FAILED", title="t", body="b", dedupe="probe:1", provider_id=world["p1"]) == 1
            assert exam_notify.notify(db, [sid], kind="EXAM_PUBLISH_FAILED", title="t", body="b", dedupe="probe:1", provider_id=world["p1"]) == 0   # idempotent
            row = db.query(ExamNotificationOutbox).one()
            assert row.status == "PENDING" and db.query(Notification).count() == 0
    finally:
        internal_http._PEERS["operations"] = real
    with service_context("exam"), db_factory() as db:
        assert exam_notify.deliver_pending(db, now=utcnow().replace(year=2100))["sent"] == 1
        assert db.query(Notification).count() == 1
        assert exam_notify.deliver_pending(db, now=utcnow().replace(year=2100))["sent"] == 0


# ------------------------------------------------------------------ gateway + parity with the legacy monolith

def test_gateway_routing_is_unambiguous():
    from institute_common.gateway import ambiguities
    assert ambiguities(TABLE) == []


def test_staff_lookup_is_not_swallowed_by_the_parameterised_route():
    from institute_common.gateway import resolve
    assert resolve(TABLE, "GET", f"{API}/staff/lookup") == "identity"
    assert resolve(TABLE, "GET", f"{API}/providers/{uuid.uuid4()}/subscription") == "operations"
    assert resolve(TABLE, "GET", f"{API}/providers/{uuid.uuid4()}") == "organization"
    assert resolve(TABLE, "GET", f"{API}/providers/{uuid.uuid4()}/transfers") == "enrollment"
    assert resolve(TABLE, "POST", f"{API}/exams/{uuid.uuid4()}/answer-key-corrections") == "exam"
    assert resolve(TABLE, "POST", f"{API}/teaching") == "academics"
    assert resolve(TABLE, "GET", f"{API}/audit-logs") == "identity"


def test_each_public_route_is_served_by_exactly_one_service():
    seen = {}
    for e in TABLE:
        for m in e.methods:
            assert (m, e.path) not in seen, f"{m} {e.path} defined by {seen[(m, e.path)]} and {e.service}"
            seen[(m, e.path)] = e.service


# Public routes that did not exist in the monolith (new features). Adding one means adding it here on purpose.
NEW_ROUTES = {
    ("PATCH", f"{API}/enrollments/{{enrollment_id}}/academic-profile"),          # Class / Academic Year per enrollment
    ("POST", f"{API}/enrollments/{{enrollment_id}}/password-setup-link"),         # staff-initiated password setup link
    ("POST", f"{API}/paper-versions/{{pv_id}}/revisions"),
    ("POST", f"{API}/branches/{{branch_id}}/activate"),
    ("POST", f"{API}/branches/{{branch_id}}/composite-requests"), ("GET", f"{API}/branches/{{branch_id}}/composite-requests"),
    ("GET", f"{API}/composite-invitations"), ("GET", f"{API}/composite-providers"), ("GET", f"{API}/composite-requests/{{request_id}}"),
    ("POST", f"{API}/composite-requests/{{request_id}}/cancel"),
    ("POST", f"{API}/composite-requests/{{request_id}}/slots/{{slot_id}}/contribute"),
    ("POST", f"{API}/composite-requests/{{request_id}}/slots/{{slot_id}}/decline"),
    ("GET", f"{API}/system/health"),
    ("PATCH", f"{API}/subjects/{{subject_id}}"),
    # question bank, paper building, import history
    ("GET", f"{API}/questions"), ("GET", f"{API}/questions/{{question_id}}"), ("PATCH", f"{API}/questions/{{question_id}}"),
    ("GET", f"{API}/question-taxonomy"), ("POST", f"{API}/papers"), ("GET", f"{API}/papers"),
    ("GET", f"{API}/papers/{{paper_id}}/versions"), ("GET", f"{API}/uploads"),
    # student directory
    ("GET", f"{API}/students"), ("GET", f"{API}/students/{{student_id}}"),
    # test creation: audiences and Global Tests
    ("POST", f"{API}/branches/{{branch_id}}/audience-preview"), ("PUT", f"{API}/exam-plans/{{plan_id}}/audience"),
    ("POST", f"{API}/branches/{{branch_id}}/exam-plans/check"), ("POST", f"{API}/papers/check"),
    ("GET", f"{API}/tests"), ("POST", f"{API}/tests"), ("GET", f"{API}/tests/scope"), ("GET", f"{API}/tests/students"),
    ("POST", f"{API}/tests/audience"), ("POST", f"{API}/tests/check"),
    ("GET", f"{API}/global-tests"), ("POST", f"{API}/global-tests"), ("GET", f"{API}/global-tests/{{test_id}}"),
    ("PUT", f"{API}/global-tests/{{test_id}}"), ("POST", f"{API}/global-tests/{{test_id}}/publish"),
    ("POST", f"{API}/global-tests/{{test_id}}/withdraw"), ("POST", f"{API}/global-tests/{{test_id}}/derive"),
    ("POST", f"{API}/global-tests/{{test_id}}/schedule"),
    # three test types: Daily paper choice, Weekly Tests (series) and Global Test contributions
    ("GET", f"{API}/tests/papers"),
    ("POST", f"{API}/test-series/preview"), ("POST", f"{API}/test-series"), ("GET", f"{API}/test-series"),
    ("GET", f"{API}/test-series/{{series_id}}"), ("POST", f"{API}/test-series/{{series_id}}/generate"),
    ("POST", f"{API}/test-series/{{series_id}}/cancel"),
    ("GET", f"{API}/global-tests/{{test_id}}/contribution-options"), ("POST", f"{API}/global-tests/{{test_id}}/contributions"),
    ("GET", f"{API}/global-test-contributions"), ("POST", f"{API}/global-test-contributions/{{contribution_id}}/withdraw"),
    ("POST", f"{API}/global-test-contributions/{{contribution_id}}/approve"),
    ("POST", f"{API}/global-test-contributions/{{contribution_id}}/reject"),
    ("POST", f"{API}/global-test-contributions/{{contribution_id}}/retry-copy"),
    # Global Tests: central schedule and provider paper submissions
    ("GET", f"{API}/global-tests/{{test_id}}/occurrences"), ("POST", f"{API}/global-tests/{{test_id}}/occurrences"),
    ("POST", f"{API}/global-tests/{{test_id}}/occurrences/plan"), ("POST", f"{API}/global-tests/{{test_id}}/occurrences/publish"),
    ("GET", f"{API}/global-occurrences/{{occurrence_id}}"), ("PUT", f"{API}/global-occurrences/{{occurrence_id}}"),
    ("DELETE", f"{API}/global-occurrences/{{occurrence_id}}"), ("POST", f"{API}/global-occurrences/{{occurrence_id}}/cancel"),
    ("GET", f"{API}/global-occurrences/{{occurrence_id}}/scope"), ("GET", f"{API}/global-occurrences/{{occurrence_id}}/students"),
    ("GET", f"{API}/global-occurrences/{{occurrence_id}}/papers"), ("GET", f"{API}/global-occurrences/{{occurrence_id}}/submissions"),
    ("GET", f"{API}/global-occurrences/{{occurrence_id}}/student-subjects"),
    ("GET", f"{API}/exams/{{exam_id}}/integrity"), ("GET", f"{API}/exams/{{exam_id}}/integrity/{{attempt_id}}"),
    ("POST", f"{API}/exams/{{exam_id}}/integrity/{{attempt_id}}/decision"),
    ("GET", f"{API}/global-paper-submissions/{{submission_id}}/students"), ("GET", f"{API}/global-occurrences/{{occurrence_id}}/coverage/students"),
    ("POST", f"{API}/global-occurrences/{{occurrence_id}}/submissions"), ("POST", f"{API}/global-paper-submissions/{{submission_id}}/withdraw"),
    ("GET", f"{API}/global-schedule"),
    # photo papers: drafts from mobile photos with a separate answer key, previewed and confirmed before import
    ("POST", f"{API}/branches/{{branch_id}}/subjects/{{subject_id}}/photo-drafts"), ("GET", f"{API}/photo-drafts"),
    ("GET", f"{API}/photo-drafts/{{draft_id}}"), ("POST", f"{API}/photo-drafts/{{draft_id}}/files"),
    ("PUT", f"{API}/photo-drafts/{{draft_id}}/files/order"), ("DELETE", f"{API}/photo-drafts/{{draft_id}}/files/{{draft_file_id}}"),
    ("GET", f"{API}/photo-drafts/{{draft_id}}/files/{{draft_file_id}}/content"), ("POST", f"{API}/photo-drafts/{{draft_id}}/extract"),
    ("PUT", f"{API}/photo-drafts/{{draft_id}}/questions"), ("POST", f"{API}/photo-drafts/{{draft_id}}/accept"),
    ("POST", f"{API}/photo-drafts/{{draft_id}}/discard"),
}


def test_all_legacy_routes_still_exist():
    legacy_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "legacy-monolith"))
    if not os.path.isdir(legacy_dir):
        pytest.skip("legacy monolith not present")
    sys.path.insert(0, legacy_dir)
    try:
        for m in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            del sys.modules[m]
        legacy = importlib.import_module("app.main").app
        legacy_ops = {(m.upper(), p) for p, ops in legacy.openapi()["paths"].items() if p.startswith("/api/v1/") for m in ops}
    finally:
        sys.path.remove(legacy_dir)
        for m in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            del sys.modules[m]
    new_ops = {(m, e.path) for e in TABLE for m in e.methods}
    assert legacy_ops - new_ops == set(), f"routes lost in the split: {sorted(legacy_ops - new_ops)}"
    assert new_ops - legacy_ops == NEW_ROUTES, f"public routes added since the monolith must be listed deliberately: {sorted(new_ops - legacy_ops - NEW_ROUTES)}"


def test_internal_routes_exist_only_where_designed(client):
    internal = {name: sorted(p for p in app.openapi()["paths"] if p.startswith("/internal/")) for name, app in SERVICES.items()}
    assert internal["identity"] == ["/internal/v1/audit/events", "/internal/v1/introspect"]
    assert internal["academics"] == ["/internal/v1/teaching/retire"]
    assert internal["operations"] == ["/internal/v1/notifications"]
    assert internal["paper"] == ["/internal/v1/paper-shares", "/internal/v1/papers/copy-global"]
    assert internal["enrollment"] == ["/internal/v1/reference-sync"]
    assert all(not v for k, v in internal.items() if k not in {"identity", "academics", "operations", "paper", "enrollment"})


# ------------------------------------------------------------------ platform service health

def test_super_admin_sees_the_health_of_every_service_and_the_failure_queues(client, make_staff, login):
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    r = client.get(f"{API}/system/health", headers=login(sa))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["services"]) == {"identity", "organization", "academics", "enrollment", "paper", "exam", "operations"}
    assert all(s["status"] == "ok" for s in body["services"].values()) and body["healthy"] is True
    assert set(body["queues"]) == {"failed_exam_deliveries", "failed_generation_jobs", "failed_student_notifications",
                                   "failed_staff_notifications", "unfinished_result_corrections"}


def test_an_unreachable_service_and_a_failed_queue_make_the_platform_unhealthy(client, make_staff, login, db_factory):
    from institute_common import internal_http
    from institute_common.models.exams import ExamNotificationOutbox
    _, sa = make_staff([(Role.SUPER_ADMIN, None, None)])
    h = login(sa)
    with db_factory() as db:
        db.add(ExamNotificationOutbox(dedupe_key="x" * 20, payload={}, status="FAILED"))
        db.commit()
    class Down:
        def get(self, *a, **k):
            raise ConnectionError("connection refused")

    gone = internal_http._PEERS["paper"]
    internal_http._PEERS["paper"] = Down()
    try:
        body = client.get(f"{API}/system/health", headers=h).json()
    finally:
        internal_http._PEERS["paper"] = gone
    assert body["services"]["paper"]["status"] == "unreachable" and body["queues"]["failed_staff_notifications"] == 1 and body["healthy"] is False


@pytest.mark.parametrize("role", [Role.ADMIN, Role.PROVIDER_ADMIN, Role.BRANCH_ADMIN, Role.TEACHER])
def test_service_health_is_for_the_platform_owner_only(client, world, make_staff, login, role):
    scope = {Role.ADMIN: (None, None), Role.PROVIDER_ADMIN: (world["p1"], None), Role.BRANCH_ADMIN: (world["p1"], world["p1b1"]), Role.TEACHER: (world["p1"], None)}[role]
    _, code = make_staff([(role, *scope)])
    assert client.get(f"{API}/system/health", headers=login(code)).status_code == 403


def test_migration_revision_ids_fit_the_alembic_version_column():
    """alembic_version.version_num is varchar(32) on PostgreSQL: a longer revision id fails only there (SQLite does not enforce it)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    ids = {f.name: re.search(r'^revision = "([^"]+)"', f.read_text(), re.M).group(1) for f in root.glob("institute-*-service/alembic/versions/*.py")}
    assert ids and not {name: rid for name, rid in ids.items() if len(rid) > 32}
