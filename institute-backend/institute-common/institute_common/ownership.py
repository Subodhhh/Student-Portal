"""Single registry of which service WRITES which table.

Rules enforced at run time (SQLAlchemy `before_flush` guard) and in tests:
  * a service may INSERT/UPDATE/DELETE only rows of tables it owns;
  * everything else is changed through the owning service's internal API (`/internal/v1`, service key);
  * reading another service's tables is still allowed in this phase (shared PostgreSQL); those reads are explicit technical debt,
    listed in docs/institute-portal/MICROSERVICES.md, to be replaced by APIs before the databases are separated.

`STUDENT_CHAIN_TABLES` are created/altered only by the existing Student enrollment migration chain; the Institute services write
some of them (as the monolith did) but never run DDL on them.
"""
from sqlalchemy import event

from institute_common.context import current_service

OWNED: dict[str, frozenset[str]] = {
    "identity": frozenset({"staff", "staff_tokens", "staff_role_assignments", "institute_audit_logs"}),
    "organization": frozenset({"providers", "branches"}),
    "academics": frozenset({"subjects", "provider_subjects", "batch_subjects", "teacher_subject_assignments", "batches"}),
    "enrollment": frozenset({"enrollments", "enrollment_batches", "enrollment_events", "enrollment_transfers"}),
    "paper": frozenset({"stored_files", "bank_questions", "question_versions", "paper_uploads", "papers", "paper_versions",
                        "paper_version_questions", "paper_version_events", "paper_shares", "global_paper_copies",
                        "paper_photo_drafts", "paper_photo_draft_files"}),
    "exam": frozenset({"exam_plans", "exam_plan_batches", "exam_plan_subjects", "exam_generation_jobs", "exam_generation_events", "exams",
                       "exam_versions", "exam_version_papers", "student_exam_assignments", "exam_outbox", "student_notification_outbox",
                       "result_corrections", "exam_notification_outbox", "composite_requests", "composite_request_slots",
                       "exam_plan_students", "exam_plan_branches", "global_tests", "global_test_contributions", "global_test_sections",
                       "test_series", "global_test_subjects", "global_test_occurrences", "global_paper_submissions"}),
    "operations": frozenset({"institute_notifications", "support_tickets", "support_ticket_messages", "subscription_plans", "provider_subscriptions",
                             "invoices", "payment_records", "billing_adjustments", "institute_settings"}),
}
SPOOL_PREFIX = "inst_"          # per-service audit spool tables: inst_<service>_audit_spool (owned by that service)
STUDENT_CHAIN_TABLES = frozenset({"providers", "branches", "batches", "enrollments", "enrollment_batches", "students"})


class OwnershipViolation(RuntimeError):
    pass


def owned_tables(service: str) -> frozenset[str]:
    return OWNED[service] | {f"{SPOOL_PREFIX}{service}_audit_spool"}


def owner_of(table: str) -> str | None:
    for svc, tables in OWNED.items():
        if table in tables:
            return svc
    for svc in OWNED:
        if table == f"{SPOOL_PREFIX}{svc}_audit_spool":
            return svc
    return None


def attach_guard(session, service: str | None = None) -> None:
    """Make `session` refuse to flush rows of tables the (current) service does not own."""
    service = service or current_service()
    if service is None:          # CLI/migration contexts that run outside a service
        return

    @event.listens_for(session, "before_flush")
    def _guard(sess, flush_context, instances):
        for obj in list(sess.new) + list(sess.dirty) + list(sess.deleted):
            if obj in sess.dirty and not sess.is_modified(obj):
                continue
            table = getattr(getattr(obj, "__table__", None), "name", None)
            if table and table not in owned_tables(service):
                raise OwnershipViolation(f"service '{service}' may not write table '{table}' (owner: {owner_of(table) or 'unowned'})")
