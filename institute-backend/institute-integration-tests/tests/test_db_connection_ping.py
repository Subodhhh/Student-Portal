"""Pooled connections are checked (one round trip) only after sitting idle; a dead one is discarded so the pool reconnects."""
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import exc

from institute_common import database
from institute_common.config import settings


class FakeConnection:
    def __init__(self, fail=False):
        self.fail, self.pinged = fail, 0

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
    assert alive.pinged == 1
    dead = FakeConnection(fail=True)
    with pytest.raises(exc.DisconnectionError):            # the pool then opens a fresh connection (pessimistic disconnect handling)
        database._ping_if_idle(dead, record(settings.db_ping_after_idle_seconds + 1), None)


def test_checkin_records_when_the_connection_went_idle():
    r = record(None)
    database._checked_in(None, r)
    assert time.monotonic() - r.info["idle_since"] < 1
