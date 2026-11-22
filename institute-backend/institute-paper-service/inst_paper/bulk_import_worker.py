"""Bulk question import worker: runs in its OWN container (never inside the API), so reading and importing large files never slows
down the paper service or the exams students are taking.

    python -m inst_paper.bulk_import_worker run-once     claim and process at most one job, then housekeeping
    python -m inst_paper.bulk_import_worker loop         keep doing that; sleeps a few seconds when there is nothing to do

The database is the queue (bulk_import_jobs): no Redis is needed, and a restarted worker simply claims again - an interrupted job is
resumed by whichever worker claims it after its lease expires (services/bulk_import.py). Several workers may run side by side.
"""
import logging
import os
import socket
import sys
import time

from institute_common.context import service_context
from institute_common.database import new_session
from institute_common.objectstore import get_object_store
from inst_paper.services import bulk_import, notify

log = logging.getLogger("inst_paper.bulk_import_worker")
IDLE_SECONDS = 5
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def run_once() -> dict:
    """Claim and process one job (if any), then expire old previews and retry postponed cleanups."""
    store = get_object_store()
    out: dict = {}
    with service_context("paper"), new_session() as db:
        job = bulk_import.claim_next(db, WORKER_ID)
        if job is not None:
            out["job"] = str(job.job_id)
            bulk_import.run_job(db, job, store, WORKER_ID)
            db.refresh(job)
            out["status"] = job.status
        try:
            out.update(bulk_import.housekeeping(db, store))
        except Exception:                                   # noqa: BLE001  (housekeeping never stops the worker)
            db.rollback()
            log.exception("bulk import housekeeping failed")
        try:
            out["notifications"] = notify.deliver_pending(db)
        except Exception:                                   # noqa: BLE001  (an inbox outage never stops imports; retried next pass)
            db.rollback()
            log.exception("bulk import notification delivery failed")
    return out


def loop() -> None:
    while True:
        result = run_once()
        if "job" not in result:
            time.sleep(IDLE_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ["run-once"]:
        print(run_once())
    elif sys.argv[1:] == ["loop"]:
        loop()
    else:
        raise SystemExit(__doc__)
