"""Audit spool delivery under retries, partial delivery and competing forwarders (two processes, or two deployments sharing one
database). Every spooled event reaches the identity log exactly once and is never lost. The concurrency tests need a disposable
PostgreSQL (TEST_DATABASE_URL); SQLite has no row locks."""
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from institute_common.audit_forward import forward_spool, ingest_events
from institute_common.config import settings
from institute_common.context import service_context
from institute_common.core import audit
from institute_common.internal_http import InternalHttp, InternalServiceError
from institute_common.models.audit_spool import SPOOLS
from institute_common.models.staff import AuditLog

SPOOL = SPOOLS["paper"]
needs_pg = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs a disposable PostgreSQL (TEST_DATABASE_URL)")


def identity_http() -> InternalHttp:
    from tests.gateway import identity
    return InternalHttp("", "test-internal-key", client=TestClient(identity))


def spool_events(db_factory, monkeypatch, n: int) -> list[dict]:
    monkeypatch.setattr(settings, "audit_forward_inline", False)
    with service_context("paper"), db_factory() as db:
        for i in range(n):
            audit.record(db, "paper.probe", "SUCCESS", details={"i": i})
    with db_factory() as db:
        return [{"event_id": str(r.event_id), **r.payload} for r in db.query(SPOOL).order_by(SPOOL.created_at)]


def delivered(db_factory) -> tuple[int, int]:
    """(audit rows for the probe events, spool rows still pending)."""
    with db_factory() as db:
        return db.query(AuditLog).filter_by(action="paper.probe").count(), db.query(SPOOL).filter(SPOOL.forwarded_at.is_(None)).count()


def test_replays_and_duplicates_within_a_batch_insert_each_event_once(client, db_factory, monkeypatch):
    events = spool_events(db_factory, monkeypatch, 3)
    with db_factory() as db:
        assert ingest_events(db, "paper", events + [events[0]]) == 3
        assert ingest_events(db, "paper", events) == 0
    assert delivered(db_factory)[0] == 3


def test_a_partly_delivered_batch_completes_without_duplicates(client, db_factory, monkeypatch):
    events = spool_events(db_factory, monkeypatch, 5)
    with db_factory() as db:
        assert ingest_events(db, "paper", events[:2]) == 2                    # e.g. delivered earlier by another forwarder
        assert forward_spool(db, "paper", identity_http()) == {"sent": 5, "pending": 0}
    assert delivered(db_factory) == (5, 0)


def test_a_failed_or_timed_out_delivery_is_retried_and_nothing_is_lost(client, db_factory, monkeypatch):
    spool_events(db_factory, monkeypatch, 4)
    real = identity_http()

    class Flaky:
        calls = 0

        def request(self, *a, **k):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise InternalServiceError("service unavailable")             # what InternalHttp raises after timeouts
            if Flaky.calls == 2:
                real.request(*a, **k)                                         # identity stored the batch, the answer was lost
                raise InternalServiceError("service unavailable")
            return real.request(*a, **k)

    with db_factory() as db:
        assert forward_spool(db, "paper", Flaky()) == {"sent": 0, "pending": 4}
        assert forward_spool(db, "paper", Flaky()) == {"sent": 0, "pending": 4}
        assert {r.attempts for r in db.query(SPOOL)} == {2}
        assert forward_spool(db, "paper", Flaky()) == {"sent": 4, "pending": 0}
    assert delivered(db_factory) == (4, 0)


def test_ingestion_is_one_lookup_and_one_insert_per_batch(client, db_factory, monkeypatch):
    from sqlalchemy import event
    events = spool_events(db_factory, monkeypatch, 30)
    with db_factory() as db:
        statements = []
        listener = lambda *a, **k: statements.append(1)  # noqa: E731
        event.listen(db.get_bind(), "before_cursor_execute", listener)
        try:
            assert ingest_events(db, "paper", events) == 30
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", listener)
    assert len(statements) <= 3                                               # not one round trip per event


@needs_pg
def test_competing_forwarders_never_send_the_same_event_twice(client, db_factory, monkeypatch):
    import institute_common.audit_forward as af
    monkeypatch.setattr(af, "BATCH", 20)
    events = spool_events(db_factory, monkeypatch, 90)
    sent: list[str] = []
    lock, start = threading.Lock(), threading.Barrier(3)

    class Slow:
        def __init__(self):
            self.real = identity_http()

        def request(self, method, path, json):
            with lock:
                sent.extend(e["event_id"] for e in json["events"])
            time.sleep(0.2)                                                   # keep the claim held while the other forwarder runs
            return self.real.request(method, path, json=json)

    errors = []

    def forwarder():
        try:
            start.wait()
            http = Slow()
            with db_factory() as db:
                while forward_spool(db, "paper", http)["sent"]:
                    pass
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=forwarder) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors
    assert len(sent) == len(set(sent)) == 90                                  # each event sent by exactly one forwarder
    assert set(sent) == {e["event_id"] for e in events}
    assert delivered(db_factory) == (90, 0)


@needs_pg
def test_two_senders_ingesting_the_same_events_at_once_both_succeed(client, db_factory, monkeypatch):
    events = spool_events(db_factory, monkeypatch, 40)
    start, results, errors = threading.Barrier(2), [], []

    def send():
        try:
            start.wait()
            with db_factory() as db:
                results.append(ingest_events(db, "paper", events))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors                                                         # no primary-key violation
    assert sum(results) == 40
    assert delivered(db_factory)[0] == 40
