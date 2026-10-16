"""Append-only guarantees enforced by PostgreSQL triggers; skipped on the default SQLite run
(use scripts/verify-institute-on-postgres.sh, which sets TEST_DATABASE_URL to a disposable database)."""
import os
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_exams import ctx, pa  # noqa: F401  (fixtures)
from tests.test_result_corrections import adjust, fakes, super_admin  # noqa: F401  (fixtures)
from tests.test_student_integration import live_exam  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs a disposable PostgreSQL (TEST_DATABASE_URL)")


def test_result_correction_request_reason_and_actor_cannot_be_rewritten_or_deleted(client, live_exam, fakes, super_admin, db_factory):  # noqa: F811
    r = adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    assert r.status_code == 200, r.text
    cid = UUID(r.json()["correction_id"])
    for stmt in ("UPDATE result_corrections SET reason = 'edited later' WHERE correction_id = :c",
                 "UPDATE result_corrections SET request = '{}' WHERE correction_id = :c",
                 "UPDATE result_corrections SET actor_staff_id = gen_random_uuid() WHERE correction_id = :c",
                 "DELETE FROM result_corrections WHERE correction_id = :c"):
        with db_factory() as db:
            with pytest.raises(DBAPIError):
                db.execute(text(stmt), {"c": cid})
                db.commit()
    with db_factory() as db:   # the execution state remains writable (that is how a retry completes)
        db.execute(text("UPDATE result_corrections SET status = 'FAILED', error = 'x' WHERE correction_id = :c"), {"c": cid})
        db.commit()
        assert db.execute(text("SELECT status FROM result_corrections WHERE correction_id = :c"), {"c": cid}).scalar() == "FAILED"


def test_audit_log_is_append_only(client, live_exam, fakes, super_admin, db_factory):  # noqa: F811
    adjust(client, live_exam["exam_id"], "STU000001", 9, super_admin["h"]())
    with db_factory() as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE institute_audit_logs SET reason = 'tampered' WHERE action = 'result.adjust'"))
            db.commit()
