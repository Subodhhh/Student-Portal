"""Background tasks, run from cron / a scheduler container:
    python -m app.worker run-once
Generates due exam plans and delivers the outbox to the Student test service. Safe to run
concurrently or repeatedly (every step is idempotent)."""
import sys

from app.database import SessionLocal
from app.services import assignment_sync, exam_generation, outbox, student_notifications


def run_once() -> dict:
    """One pass of every background step; each is idempotent so passes may overlap or repeat."""
    with SessionLocal() as db:
        jobs = exam_generation.run_due_plans(db)
        delivery = outbox.deliver_due(db)                      # exams -> student services (queues the "new test" notice)
        sync = assignment_sync.sync_open_exams(db)             # STARTED / COMPLETED / MISSED from student attempts
        results_queued = assignment_sync.notify_results(db)    # "your result is ready", once per student
        notices = student_notifications.deliver_due(db)        # -> student inbox
    return {"generated": [(str(j.plan_id), j.status) for j in jobs], "delivery": delivery, "sync": sync,
            "results_notified": results_queued, "student_notifications": notices}


if __name__ == "__main__":
    if sys.argv[1:] != ["run-once"]:
        raise SystemExit(__doc__)
    print(run_once())
