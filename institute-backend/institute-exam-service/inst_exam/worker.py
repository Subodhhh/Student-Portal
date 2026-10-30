"""Background tasks, run from cron / a scheduler container:
    python -m inst_exam.worker run-once
Generates due exam plans and delivers the outbox to the Student test service. Safe to run
concurrently or repeatedly (every step is idempotent)."""
import logging
import sys

from institute_common.audit_forward import forward_spool
from institute_common.context import service_context
from institute_common.database import new_session
from inst_exam.services import assignment_sync, composite_expiry, exam_generation, global_generation, notify, outbox, student_notifications

log = logging.getLogger("inst_exam.worker")


def run_once() -> dict:
    """One pass of every background step; each is idempotent so passes may overlap or repeat. A step that fails is rolled back and
    logged without stopping the others (its own bounded retries decide when it gives up)."""
    out: dict = {}
    with service_context("exam"), new_session() as db:
        def step(name, fn):
            try:
                out[name] = fn(db)
            except Exception:  # noqa: BLE001  (one failing step never blocks the rest of the pass)
                db.rollback()
                log.exception("worker step %s failed", name)
                out[name] = "FAILED"
        step("generated", lambda db: [(str(j.plan_id), j.status) for j in exam_generation.run_due_plans(db)])
        step("global_generated", lambda db: len(global_generation.run_due(db)))     # Global Test dates past their submission deadline
        step("composite_expired", composite_expiry.expire_due)                      # unanswered composite invitations past their deadline
        step("delivery", outbox.deliver_due)                                         # exams -> student services (queues the "new test" notice)
        step("sync", assignment_sync.sync_open_exams)                                # STARTED / COMPLETED / MISSED from student attempts
        step("results_notified", assignment_sync.notify_results)                     # "your result is ready", once per student
        step("student_notifications", student_notifications.deliver_due)             # -> student inbox
        step("staff_notifications", notify.deliver_pending)                          # -> operations service (staff inbox)
        step("audit", lambda db: forward_spool(db, "exam"))                          # -> identity service (audit log)
    return out

if __name__ == "__main__":
    if sys.argv[1:] != ["run-once"]:
        raise SystemExit(__doc__)
    print(run_once())
