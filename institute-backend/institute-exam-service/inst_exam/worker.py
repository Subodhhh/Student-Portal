"""Background tasks, run from cron / a scheduler container:
    python -m inst_exam.worker run-once
Generates due exam plans and delivers the outbox to the Student test service. Safe to run
concurrently or repeatedly (every step is idempotent)."""
import sys

from institute_common.audit_forward import forward_spool
from institute_common.context import service_context
from institute_common.database import new_session
from inst_exam.services import assignment_sync, composite_expiry, exam_generation, global_generation, notify, outbox, student_notifications


def run_once() -> dict:
    """One pass of every background step; each is idempotent so passes may overlap or repeat."""
    with service_context("exam"), new_session() as db:
        jobs = exam_generation.run_due_plans(db)
        global_runs = global_generation.run_due(db)            # Global Test dates past their submission deadline
        expired = composite_expiry.expire_due(db)              # unanswered composite invitations past their deadline
        delivery = outbox.deliver_due(db)                      # exams -> student services (queues the "new test" notice)
        sync = assignment_sync.sync_open_exams(db)             # STARTED / COMPLETED / MISSED from student attempts
        results_queued = assignment_sync.notify_results(db)    # "your result is ready", once per student
        notices = student_notifications.deliver_due(db)        # -> student inbox
        staff_notices = notify.deliver_pending(db)             # -> operations service (staff inbox)
        audit = forward_spool(db, "exam")                      # -> identity service (audit log)
    return {"composite_expired": expired, "staff_notifications": staff_notices, "audit": audit, "generated": [(str(j.plan_id), j.status) for j in jobs], "global_generated": len(global_runs), "delivery": delivery, "sync": sync,
            "results_notified": results_queued, "student_notifications": notices}


if __name__ == "__main__":
    if sys.argv[1:] != ["run-once"]:
        raise SystemExit(__doc__)
    print(run_once())
