"""Institute -> Student read projection (providers, branches, batches, enrollments, batch membership).

The receiver is the Student enrollment service's internal API; here it is replaced by a recorder so the publisher's contract can be
checked precisely: parents before children, only changed rows after the first pass, membership travels as a complete set,
rejected rows are retried, transport failures are reported without crashing."""
import pytest

from institute_common.internal_http import InternalServiceError
from institute_common.models.org import Batch, Enrollment, EnrollmentBatch
from inst_enrollment.services import reference_sync


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Recorder:
    """Stands in for InternalHttp to the Student enrollment service."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reject: set[tuple[str, str]] = set()
        self.down = False

    def request(self, method, path, json=None, **kw):
        if self.down:
            raise InternalServiceError("service unavailable")
        assert (method, path) == ("POST", "/reference/sync")
        self.calls.append(json)
        errors = []
        for kind, rows in json.items():
            single = reference_sync.SINGULAR[kind]
            for row in rows:
                rid = row[reference_sync.ID_FIELD[kind]]
                if (single, rid) in self.reject:
                    errors.append({"kind": single, "id": rid, "reason": "constraint", "constraint": "x"})
        return FakeResponse(200, {"applied": {}, "errors": errors})

    def sent(self, kind):
        return [row for call in self.calls for row in call.get(kind, [])]


@pytest.fixture()
def rec():
    r = Recorder()
    reference_sync.set_client(r)
    reference_sync.reset_cache()
    yield r
    reference_sync.set_client(None)
    reference_sync.reset_cache()


@pytest.fixture()
def roster(world, db_factory):
    with db_factory() as db:
        batch = Batch(branch_id=world["p1b1"], name="Morning")
        db.add(batch)
        db.flush()
        e = Enrollment(enrollment_id="e-1", provider_id=world["p1"], branch_id=world["p1b1"], student_id="STU000042", status="ACTIVE")
        db.add(e)
        db.flush()
        db.add(EnrollmentBatch(enrollment_id="e-1", batch_id=batch.batch_id))
        db.commit()
        return {"batch": batch.batch_id, **world}


def test_first_pass_pushes_everything_parents_first(rec, roster, db_factory):
    with db_factory() as db:
        out = reference_sync.run_once(db)
    assert out["rejected"] == 0 and out["sent"] == out["pending"] == 2 + 4 + 1 + 1
    kinds = [next(iter(c)) for c in rec.calls]
    assert kinds == ["providers", "branches", "batches", "enrollments"]
    (enr,) = rec.sent("enrollments")
    assert enr["student_id"] == "STU000042" and [m["batch_id"] for m in enr["batches"]] == [str(roster["batch"])]
    assert reference_sync.status()["last_error"] is None


def test_second_pass_sends_only_changes(rec, roster, db_factory):
    with db_factory() as db:
        reference_sync.run_once(db)
    rec.calls.clear()
    with db_factory() as db:
        assert reference_sync.run_once(db)["pending"] == 0
    assert rec.calls == []
    with db_factory() as db:
        db.get(Enrollment, "e-1").status = "INACTIVE"
        db.commit()
        out = reference_sync.run_once(db)
    assert out["sent"] == 1 and [r["status"] for r in rec.sent("enrollments")] == ["INACTIVE"]


def test_removing_a_batch_membership_is_propagated_as_a_complete_set(rec, roster, db_factory):
    with db_factory() as db:
        reference_sync.run_once(db)
    rec.calls.clear()
    with db_factory() as db:
        db.delete(db.query(EnrollmentBatch).one())
        db.commit()
        reference_sync.run_once(db)
    (enr,) = rec.sent("enrollments")
    assert enr["batches"] == []


def test_rejected_rows_are_retried_and_reported(rec, roster, db_factory):
    rec.reject = {("enrollment", "e-1")}
    with db_factory() as db:
        out = reference_sync.run_once(db)
    assert out["rejected"] == 1 and reference_sync.status()["rejected"][0]["id"] == "e-1"
    rec.calls.clear()
    rec.reject = set()
    with db_factory() as db:
        out = reference_sync.run_once(db)
    assert out == {"pending": 1, "sent": 1, "rejected": 0}


def test_transport_failure_is_reported_not_raised_and_everything_is_resent(rec, roster, db_factory):
    rec.down = True
    with db_factory() as db:
        out = reference_sync.run_once(db)
    assert "error" in out and reference_sync.status()["last_error"]
    rec.down = False
    with db_factory() as db:
        assert reference_sync.run_once(db)["sent"] == 8


def test_unconfigured_receiver_is_reported(world, db_factory):
    reference_sync.set_client(None)
    with db_factory() as db:
        assert reference_sync.run_once(db)["error"] == "not_configured"


def test_internal_endpoint_needs_the_service_key(client):
    from tests.gateway import SERVICES
    from fastapi.testclient import TestClient
    c = TestClient(SERVICES["enrollment"])
    assert c.get("/internal/v1/reference-sync").status_code == 401
    assert c.get("/internal/v1/reference-sync", headers={"X-Service-Key": "test-internal-key"}).status_code == 200
