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
from tests.test_question_bank import bank  # noqa: F401  (fixture)
from tests.test_review_fixes import gdt  # noqa: F401  (fixture)

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


def test_published_paper_history_is_append_only(client, world, db_factory):  # noqa: F811
    """question_versions / paper_version_questions / paper_version_events reject UPDATE and DELETE (the reason corrections create new versions)."""
    from tests.test_exams import make_paper
    from institute_common.models.academics import Subject
    with db_factory() as db:
        s = Subject(code="TRG", name="Trigger")
        db.add(s)
        db.commit()
        sid = s.subject_id
    make_paper(db_factory, world["p1"], world["p1b1"], sid, "Immutable", "PUBLISHED")
    for stmt in ("UPDATE question_versions SET stem = 'rewritten'", "DELETE FROM question_versions",
                 "UPDATE paper_version_questions SET position = 99", "DELETE FROM paper_version_questions"):
        with db_factory() as db:
            with pytest.raises(DBAPIError):
                db.execute(text(stmt))
                db.commit()


def test_exam_settings_are_part_of_the_immutable_manifest(client, live_exam, db_factory):  # noqa: F811
    """The marking and policies an exam was generated with (exam_versions.settings) can never be rewritten."""
    for stmt in ("UPDATE exam_versions SET settings = '{\"marking\": {\"mode\": \"UNIFORM\", \"correct\": 1, \"wrong\": 0}}'",
                 "UPDATE exam_versions SET settings = NULL"):
        with db_factory() as db:
            with pytest.raises(DBAPIError):
                db.execute(text(stmt))
                db.commit()


def _sql_fails(db_factory, stmt, **params):
    with db_factory() as db:
        with pytest.raises(DBAPIError):
            db.execute(text(stmt), params)
            db.commit()


def test_a_published_global_test_and_its_sections_are_frozen(client, bank, gdt, login, db_factory):  # noqa: F811
    t = UUID(gdt["test_id"])
    for stmt in ("UPDATE global_tests SET duration_minutes = 99 WHERE test_id = :t",
                 "UPDATE global_tests SET name = 'renamed' WHERE test_id = :t",
                 "UPDATE global_tests SET status = 'DRAFT' WHERE test_id = :t",
                 "DELETE FROM global_tests WHERE test_id = :t",
                 "UPDATE global_test_sections SET position = 7 WHERE test_id = :t",
                 "DELETE FROM global_test_sections WHERE test_id = :t",
                 "INSERT INTO global_test_sections (section_id, test_id, position, subject_id, paper_version_id) "
                 "SELECT gen_random_uuid(), test_id, 9, subject_id, paper_version_id FROM global_test_sections WHERE test_id = :t"):
        _sql_fails(db_factory, stmt, t=t)
    # withdrawal is the one allowed change; the configuration still cannot be edited during it, and afterwards nothing changes
    _sql_fails(db_factory, "UPDATE global_tests SET status = 'WITHDRAWN', duration_minutes = 5 WHERE test_id = :t", t=t)
    assert client.post(f"{API}/global-tests/{gdt['test_id']}/withdraw", json={"reason": "Answer key error"}, headers=login(bank["sa"])).status_code == 200
    for stmt in ("UPDATE global_tests SET status = 'PUBLISHED' WHERE test_id = :t", "UPDATE global_tests SET withdrawn_at = NULL WHERE test_id = :t",
                 "DELETE FROM global_tests WHERE test_id = :t"):
        _sql_fails(db_factory, stmt, t=t)


def test_draft_global_tests_stay_editable_and_publishable(client, bank, gdt, login, db_factory):  # noqa: F811
    h = login(bank["sa"])
    d = client.post(f"{API}/global-tests/{gdt['test_id']}/derive", headers=h).json()        # a derivative is its own DRAFT record
    assert d["status"] == "DRAFT" and d["derived_from_test_id"] == gdt["test_id"]
    with db_factory() as db:   # draft edits and the DRAFT -> PUBLISHED transition pass the guards
        db.execute(text("UPDATE global_tests SET duration_minutes = 45 WHERE test_id = :t"), {"t": UUID(d["test_id"])})
        db.execute(text("UPDATE global_test_sections SET position = 2 WHERE test_id = :t"), {"t": UUID(d["test_id"])})
        db.commit()
    r = client.put(f"{API}/global-tests/{d['test_id']}", headers=h, json={"name": "Daily #1 (long)", "duration_minutes": 60,
                                                                            "sections": [{"paper_version_id": gdt["pv"]}]})
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/global-tests/{d['test_id']}/publish", headers=h).json()["status"] == "PUBLISHED"
    _sql_fails(db_factory, "UPDATE global_tests SET duration_minutes = 61 WHERE test_id = :t", t=UUID(d["test_id"]))


API = "/api/v1"
