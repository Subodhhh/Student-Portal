"""Helpers for every service's Alembic baseline.

The Institute tables already exist in deployed databases (created by the legacy `m10_alembic_version` chain). Each service therefore
starts its own chain with an ADOPT-OR-CREATE baseline: tables that are missing are created from the models (so a fresh database works),
tables that exist are left untouched, and the append-only triggers are (re)installed idempotently. Nothing is ever dropped or rewritten.
Later revisions are ordinary forward-only migrations of the tables that service owns.

Tables whose DDL belongs to the Student enrollment chain (providers, branches, batches, enrollments, enrollment_batches, students) are
never created or altered here."""
from sqlalchemy import inspect

from institute_common.database import Base
from institute_common.ownership import STUDENT_CHAIN_TABLES, owned_tables

HISTORY_FN = """
CREATE OR REPLACE FUNCTION institute_history_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql;
"""
AUDIT_FN = """
CREATE OR REPLACE FUNCTION institute_audit_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'institute_audit_logs is append-only'; END;
$$ LANGUAGE plpgsql;
"""

# A Global Daily Test is editable only as a DRAFT. Publishing (DRAFT -> PUBLISHED) is allowed; afterwards its configuration never
# changes, the only transition is PUBLISHED -> WITHDRAWN (with withdrawn_at), and a published or withdrawn test is never deleted.
GLOBAL_TEST_FN = """
CREATE OR REPLACE FUNCTION institute_global_test_guard() RETURNS trigger AS $$
BEGIN
  IF OLD.status = 'DRAFT' THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'PUBLISHED' AND NEW.status = 'WITHDRAWN'
     AND ROW(NEW.test_id, NEW.name, NEW.description, NEW.exam_type, NEW.target_class, NEW.duration_minutes, NEW.derived_from_test_id,
             NEW.created_by, NEW.created_at, NEW.published_by, NEW.published_at)
         IS NOT DISTINCT FROM
         ROW(OLD.test_id, OLD.name, OLD.description, OLD.exam_type, OLD.target_class, OLD.duration_minutes, OLD.derived_from_test_id,
             OLD.created_by, OLD.created_at, OLD.published_by, OLD.published_at) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'global test % is % and cannot be changed', OLD.test_id, OLD.status;
END;
$$ LANGUAGE plpgsql;
"""
# Sections follow their test: added, changed or removed only while the test (old and new parent) is a DRAFT.
GLOBAL_SECTION_FN = """
CREATE OR REPLACE FUNCTION institute_global_test_section_guard() RETURNS trigger AS $$
DECLARE st text;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT status INTO st FROM global_tests WHERE test_id = OLD.test_id;
    IF st IS DISTINCT FROM 'DRAFT' THEN RAISE EXCEPTION 'sections of global test % are frozen (%)', OLD.test_id, st; END IF;
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    SELECT status INTO st FROM global_tests WHERE test_id = NEW.test_id;
    IF st IS DISTINCT FROM 'DRAFT' THEN RAISE EXCEPTION 'sections of global test % are frozen (%)', NEW.test_id, st; END IF;
    RETURN NEW;
  END IF;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;
"""
FUNCTIONS = {"institute_audit_immutable": AUDIT_FN, "institute_history_immutable": HISTORY_FN,
             "institute_global_test_guard": GLOBAL_TEST_FN, "institute_global_test_section_guard": GLOBAL_SECTION_FN}

# service -> list of (trigger name, table, "UPDATE ..." clause, function)
TRIGGERS: dict[str, list[tuple[str, str, str, str]]] = {
    "identity": [("institute_audit_logs_immutable", "institute_audit_logs", "UPDATE OR DELETE", "institute_audit_immutable")],
    "paper": [(f"{t}_immutable", t, "UPDATE OR DELETE", "institute_history_immutable")
              for t in ("paper_version_events", "question_versions", "paper_version_questions")],
    "exam": [
        ("exam_versions_immutable", "exam_versions",
         "UPDATE OF exam_id, version_no, duration_minutes, start_at, end_at, total_questions, total_marks, missing_subjects, generation_job_id, settings OR DELETE",
         "institute_history_immutable"),
        ("exam_version_papers_immutable", "exam_version_papers", "UPDATE OR DELETE", "institute_history_immutable"),
        ("global_tests_guard", "global_tests", "UPDATE OR DELETE", "institute_global_test_guard"),
        ("global_test_sections_guard", "global_test_sections", "INSERT OR UPDATE OR DELETE", "institute_global_test_section_guard"),
        ("result_corrections_immutable", "result_corrections",
         "UPDATE OF correction_id, exam_id, exam_version_id, provider_id, branch_id, kind, student_id, idempotency_key, request, reason, actor_staff_id, created_at OR DELETE",
         "institute_history_immutable"),
    ],
    "operations": [(f"{t}_immutable", t, "UPDATE OR DELETE", "institute_history_immutable") for t in ("payment_records", "billing_adjustments")],
}


# Tables introduced by a LATER revision of their service (explicit op.create_table there); the baseline must not create them.
LATER_TABLES = frozenset({"composite_requests", "composite_request_slots"})
# exam_plan_students / global_tests / global_test_sections (exam 0004) are NOT listed: exam_plans references global_tests, so a fresh
# database must create them together in the baseline; 0004 creates them only where they are missing (existing databases).


def ddl_tables(service: str):
    names = owned_tables(service) - STUDENT_CHAIN_TABLES - LATER_TABLES
    return [t for t in Base.metadata.sorted_tables if t.name in names]


def adopt_or_create(bind, service: str) -> list[str]:
    """Create the service's missing tables; return their names."""
    import institute_common.models  # noqa: F401  (register every mapped table)
    existing = set(inspect(bind).get_table_names())
    missing = [t for t in ddl_tables(service) if t.name not in existing]
    Base.metadata.create_all(bind, tables=missing, checkfirst=True)
    return [t.name for t in missing]


def ensure_triggers(bind, service: str) -> None:
    if bind.dialect.name != "postgresql":
        return
    specs = TRIGGERS.get(service, [])
    if not specs:
        return
    from sqlalchemy import text
    for fn in {s[3] for s in specs}:
        bind.execute(text(FUNCTIONS[fn]))
    for name, table, clause, fn in specs:
        bind.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
        bind.execute(text(f"CREATE TRIGGER {name} BEFORE {clause} ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()"))


def baseline(bind, service: str) -> None:
    adopt_or_create(bind, service)
    ensure_triggers(bind, service)


# ---------------------------------------------------------------------------------------------------------------------------------
# Helpers for forward revisions that alter BASELINE tables. A fresh database gets those tables from the current models in the
# baseline (already containing later columns), an existing database does not; these helpers make such revisions idempotent.

def existing_columns(bind, table: str) -> set[str]:
    return {c["name"] for c in inspect(bind).get_columns(table)}


def existing_checks(bind, table: str) -> set[str]:
    return {c["name"] for c in inspect(bind).get_check_constraints(table) if c.get("name")}


def existing_indexes(bind, table: str) -> set[str]:
    return {i["name"] for i in inspect(bind).get_indexes(table)}


def nullable(bind, table: str, column: str) -> bool:
    return next(c["nullable"] for c in inspect(bind).get_columns(table) if c["name"] == column)
