"""Test start/end times leave the Student test service as AWARE UTC instants. They are stored naive in the DB session's time zone
(routes/internal._to_session_naive) and were returned naive, which browsers read as LOCAL time: a 10:00 IST exam showed as 04:30 in
India. Checked on a DISPOSABLE PostgreSQL under a UTC (Neon's GMT) and an Asia/Kolkata session zone, plus the SQLite fallback.
    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55433/postgres pytest tests/test_test_times_pg.py"""
import os
import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.routes.internal import _to_session_naive
from app.services.test_service import as_instant, format_test

URL = os.environ.get("TEST_DATABASE_URL", "")
pg = pytest.mark.skipif(not re.match(r"^postgresql(\+\w+)?://[^@/]*@(127\.0\.0\.1|localhost)", URL), reason="needs a local disposable PostgreSQL")
EXAM = datetime(2026, 10, 24, 4, 30, tzinfo=timezone.utc)          # 10:00 in Asia/Kolkata


def _session(zone: str):
    engine = create_engine(URL)

    @event.listens_for(engine, "connect")
    def _zone(dbapi_conn, _record):
        with dbapi_conn.cursor() as cur:
            cur.execute(f"SET TIME ZONE '{zone}'")
    return sessionmaker(bind=engine)()


@pg
@pytest.mark.parametrize("zone", ["GMT", "UTC", "Asia/Kolkata"])
def test_the_stored_instant_comes_back_unchanged_with_its_offset(zone):
    db = _session(zone)
    try:
        stored = _to_session_naive(db, EXAM)                         # exactly what receive_exam writes
        assert stored.tzinfo is None
        out = as_instant(db, stored)
        assert out.tzinfo is not None and out == EXAM                 # same instant, never shifted
        assert out.isoformat().endswith("+00:00")                     # what the API now sends (no browser reinterpretation)
    finally:
        db.close()


def test_sqlite_fallback_and_aware_values_pass_through():
    db = sessionmaker(bind=create_engine("sqlite://"))()
    naive_utc = EXAM.replace(tzinfo=None)
    assert as_instant(db, naive_utc) == EXAM
    assert as_instant(db, EXAM) is EXAM and as_instant(db, None) is None
    row = {"test_id": "t", "provider_id": "p", "provider_name": "P", "branch_id": "b", "branch_name": "B", "city": None, "state": None,
           "name": "N", "description": None, "duration_minutes": 60, "total_questions": 1, "total_marks": 4,
           "start_at": naive_utc, "end_at": naive_utc, "status": "ACTIVE"}
    out = format_test(row, db)
    assert out["start_at"] == EXAM and out["end_at"].tzinfo is not None
