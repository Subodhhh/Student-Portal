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


def test_rejected_rows_are_retried_and_reported(rec, roster, db_factory, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(reference_sync, "_clock", lambda: clock[0])
    rec.reject = {("enrollment", "e-1")}
    with db_factory() as db:
        out = reference_sync.run_once(db)
    assert out["rejected"] == 1 and reference_sync.status()["rejected"][0]["id"] == "e-1"
    rec.calls.clear()
    rec.reject = set()
    with db_factory() as db:                                  # unchanged and rejected moments ago: not resent yet, still reported
        assert reference_sync.run_once(db) == {"pending": 0, "sent": 0, "rejected": 0}
    assert rec.calls == [] and reference_sync.status()["rejected"][0]["id"] == "e-1"
    clock[0] += reference_sync.REJECTED_RETRY
    with db_factory() as db:
        out = reference_sync.run_once(db)
    assert out == {"pending": 1, "sent": 1, "rejected": 0}
    assert reference_sync.status()["rejected"] == []


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


# ------------------------------------------------------------------ small requests, no retry storms, deferral

def big_roster(world, db_factory, n):
    with db_factory() as db:
        batch = Batch(branch_id=world["p1b1"], name="Evening")
        db.add(batch)
        db.flush()
        for i in range(n):
            db.add(Enrollment(enrollment_id=f"bulk-{i:03d}", provider_id=world["p1"], branch_id=world["p1b1"], student_id=f"STU9{i:05d}",
                              status="ACTIVE"))
        db.commit()


def test_a_large_backlog_drains_in_small_requests_with_nothing_lost_or_duplicated(rec, world, db_factory):
    big_roster(world, db_factory, 60)
    with db_factory() as db:
        out = reference_sync.run_once(db)
    assert all(len(rows) <= reference_sync.CHUNK for call in rec.calls for rows in call.values())
    sent = [r["enrollment_id"] for r in rec.sent("enrollments")]
    assert len(sent) == len(set(sent)) == 60 and out["sent"] == out["pending"]
    rec.calls.clear()
    with db_factory() as db:
        assert reference_sync.run_once(db)["pending"] == 0


class Busy(Recorder):
    """Accepts the first `ok` requests, then answers 409 (another sync is running) or times out."""

    def __init__(self, ok, failure):
        super().__init__()
        self.ok, self.failure, self.attempts = ok, failure, 0

    def request(self, method, path, json=None, **kw):
        self.attempts += 1
        if self.attempts > self.ok:
            if self.failure == "timeout":
                raise InternalServiceError("service unavailable")
            return FakeResponse(409, {"detail": {"code": "SYNC_BUSY"}})
        return super().request(method, path, json=json, **kw)


@pytest.mark.parametrize("failure", ["busy", "timeout"])
def test_busy_or_timed_out_receiver_defers_the_rest_without_retrying_and_the_next_pass_resumes(world, db_factory, failure):
    big_roster(world, db_factory, 60)
    rec = Busy(ok=2, failure=failure)
    reference_sync.set_client(rec)
    reference_sync.reset_cache()
    try:
        with db_factory() as db:
            out = reference_sync.run_once(db)
        assert rec.attempts == 3                              # the failed request is not repeated within the pass
        assert out["deferred"] == ("busy" if failure == "busy" else "error") and reference_sync.status()["last_error"]
        first = {r["enrollment_id"] for r in rec.sent("enrollments")}
        rec.ok, rec.calls = 10**6, []
        with db_factory() as db:
            out = reference_sync.run_once(db)
        assert "deferred" not in out
        again = {r["enrollment_id"] for r in rec.sent("enrollments")}
        assert not (first & again)                            # rows accepted before the deferral are not resent
        assert first | again == {f"bulk-{i:03d}" for i in range(60)}       # nothing lost
    finally:
        reference_sync.set_client(None)
        reference_sync.reset_cache()


def test_rejected_rows_go_last_and_never_hold_up_valid_rows(rec, world, db_factory, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(reference_sync, "_clock", lambda: clock[0])
    big_roster(world, db_factory, 40)
    rec.reject = {("enrollment", f"bulk-{i:03d}") for i in range(30)}       # e.g. students that no longer exist
    with db_factory() as db:
        assert reference_sync.run_once(db)["rejected"] == 30
    with db_factory() as db:                                                 # a new valid row arrives
        db.add(Enrollment(enrollment_id="new-1", provider_id=world["p1"], branch_id=world["p1b1"], student_id="STU000777", status="ACTIVE"))
        db.commit()
    rec.calls.clear()
    clock[0] += reference_sync.REJECTED_RETRY
    with db_factory() as db:
        out = reference_sync.run_once(db)
    order = [r["enrollment_id"] for r in rec.sent("enrollments")]
    assert order[0] == "new-1" and len(order) == 31                         # the valid row first, then the 30 retries
    assert out["rejected"] == 30 and len(reference_sync.status()["rejected"]) == 20   # still reported (first 20 listed)


def test_overlapping_passes_are_skipped_not_queued(rec, roster, db_factory):
    assert reference_sync._running.acquire(blocking=False)
    try:
        with db_factory() as db:
            assert reference_sync.run_once(db)["skipped"] == "already_running"
        assert rec.calls == []
    finally:
        reference_sync._running.release()


def test_the_sync_client_never_retries_but_other_internal_calls_still_do(monkeypatch):
    import httpx
    from institute_common.config import settings
    from institute_common.internal_http import InternalHttp
    monkeypatch.setattr("institute_common.internal_http.time.sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    def client():
        return httpx.Client(base_url="http://receiver", transport=httpx.MockTransport(handler))

    monkeypatch.setattr(settings, "student_enrollment_service_url", "http://receiver")
    monkeypatch.setattr(settings, "student_enrollment_service_api_key", "k")
    reference_sync.set_client(None)
    sync = reference_sync._client()
    assert sync.retries == 1 and sync._http.timeout.read == reference_sync.TIMEOUT
    for http, expected in ((InternalHttp("", "k", client=client(), retries=sync.retries), 1), (InternalHttp("", "k", client=client()), 3)):
        calls.clear()
        with pytest.raises(InternalServiceError):
            http.request("POST", "/reference/sync", json={})
        assert len(calls) == expected


def test_a_rejected_row_that_no_longer_exists_is_no_longer_reported(rec, roster, db_factory):
    rec.reject = {("enrollment", "e-1")}
    with db_factory() as db:
        reference_sync.run_once(db)
    assert [e["id"] for e in reference_sync.status()["rejected"]] == ["e-1"]
    with db_factory() as db:
        db.query(EnrollmentBatch).filter_by(enrollment_id="e-1").delete()
        db.query(Enrollment).filter_by(enrollment_id="e-1").delete()
        db.commit()
        reference_sync.run_once(db)
    assert reference_sync.status()["rejected"] == []
