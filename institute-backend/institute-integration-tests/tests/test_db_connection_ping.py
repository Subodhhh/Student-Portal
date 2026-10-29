"""Pooled connections are checked (one round trip) only after sitting idle; a dead one is discarded so the pool reconnects."""
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import exc

from institute_common import database
from institute_common.config import settings


class FakeConnection:
    def __init__(self, fail=False):
        self.fail, self.pinged, self.rolled_back = fail, 0, 0

    def rollback(self):
        self.rolled_back += 1

    def cursor(self):
        conn = self

        class Cursor:
            def execute(self, _sql):
                conn.pinged += 1
                if conn.fail:
                    raise OSError("server closed the connection unexpectedly")

            def close(self):
                pass
        return Cursor()


def record(idle_for: float | None):
    return SimpleNamespace(info={} if idle_for is None else {"idle_since": time.monotonic() - idle_for})


def test_a_recently_used_or_new_connection_is_reused_without_a_round_trip():
    for idle in (None, 0, settings.db_ping_after_idle_seconds - 5):
        conn = FakeConnection()
        database._ping_if_idle(conn, record(idle), None)
        assert conn.pinged == 0


def test_an_idle_connection_is_pinged_and_a_dead_one_is_discarded():
    alive = FakeConnection()
    database._ping_if_idle(alive, record(settings.db_ping_after_idle_seconds + 1), None)
    assert alive.pinged == 1 and alive.rolled_back == 1        # handed out idle, not inside the ping's transaction
    dead = FakeConnection(fail=True)
    with pytest.raises(exc.DisconnectionError):            # the pool then opens a fresh connection (pessimistic disconnect handling)
        database._ping_if_idle(dead, record(settings.db_ping_after_idle_seconds + 1), None)


def test_checkin_records_when_the_connection_went_idle():
    r = record(None)
    database._checked_in(None, r)
    assert time.monotonic() - r.info["idle_since"] < 1


def test_a_pinged_connection_can_switch_to_autocommit_on_postgresql(monkeypatch):
    """The identity check switches its session to AUTOCOMMIT; after an idle ping the connection must not be left inside a transaction
    (psycopg refuses: "can't change 'autocommit' now"). Disposable PostgreSQL only (PING_TEST_DATABASE_URL, deliberately not the
    suite-wide TEST_DATABASE_URL, which truncates tables), skipped otherwise."""
    import os
    import re
    url = os.environ.get("PING_TEST_DATABASE_URL", "")
    if not (url.startswith("postgresql") and re.search(r"@(127\.0\.0\.1|localhost)[:/].*(verify|test|scratch|tmp|disposable)", url)):
        pytest.skip("needs a disposable local PostgreSQL in TEST_DATABASE_URL")
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session
    engine = create_engine(url)
    event.listen(engine, "checkin", database._checked_in)
    event.listen(engine, "checkout", database._ping_if_idle)
    monkeypatch.setattr(settings, "db_ping_after_idle_seconds", 0)          # every reuse pings
    with engine.connect() as c:
        c.execute(text("select 1"))
    with Session(engine) as db:
        db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
        assert db.execute(text("select 1")).scalar() == 1
    engine.dispose()
