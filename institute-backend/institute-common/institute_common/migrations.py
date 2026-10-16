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

# service -> list of (trigger name, table, "UPDATE ..." clause, function)
TRIGGERS: dict[str, list[tuple[str, str, str, str]]] = {
    "identity": [("institute_audit_logs_immutable", "institute_audit_logs", "UPDATE OR DELETE", "institute_audit_immutable")],
    "paper": [(f"{t}_immutable", t, "UPDATE OR DELETE", "institute_history_immutable")
              for t in ("paper_version_events", "question_versions", "paper_version_questions")],
    "exam": [
        ("exam_versions_immutable", "exam_versions",
         "UPDATE OF exam_id, version_no, duration_minutes, start_at, end_at, total_questions, total_marks, missing_subjects, generation_job_id OR DELETE",
         "institute_history_immutable"),
        ("exam_version_papers_immutable", "exam_version_papers", "UPDATE OR DELETE", "institute_history_immutable"),
        ("result_corrections_immutable", "result_corrections",
         "UPDATE OF correction_id, exam_id, exam_version_id, provider_id, branch_id, kind, student_id, idempotency_key, request, reason, actor_staff_id, created_at OR DELETE",
         "institute_history_immutable"),
    ],
    "operations": [(f"{t}_immutable", t, "UPDATE OR DELETE", "institute_history_immutable") for t in ("payment_records", "billing_adjustments")],
}


# Tables introduced by a LATER revision of their service (explicit op.create_table there); the baseline must not create them.
LATER_TABLES = frozenset({"composite_requests", "composite_request_slots"})


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
        bind.execute(text(AUDIT_FN if fn == "institute_audit_immutable" else HISTORY_FN))
    for name, table, clause, fn in specs:
        bind.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
        bind.execute(text(f"CREATE TRIGGER {name} BEFORE {clause} ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()"))


def baseline(bind, service: str) -> None:
    adopt_or_create(bind, service)
    ensure_triggers(bind, service)
