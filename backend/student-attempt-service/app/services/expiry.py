"""Server-side finalisation of attempts whose time ran out.

The deadline (`expires_at`) is fixed when the attempt starts. If the student's browser is closed when it passes, nothing would
otherwise mark the attempt as finished; this sweep does, so the result service can evaluate it and the attempt never stays
IN_PROGRESS forever. Idempotent and safe to run from several processes (a single conditional UPDATE)."""
import logging
import os
import threading

from sqlalchemy import text

from app.database import SessionLocal

log = logging.getLogger("attempt.expiry")


def finalize_expired() -> int:
    db = SessionLocal()
    try:
        n = db.execute(text("""
            UPDATE test_attempts
               SET status = 'AUTO_SUBMITTED', submitted_at = expires_at
             WHERE status = 'IN_PROGRESS' AND expires_at <= NOW()
        """)).rowcount
        db.commit()
        return n
    finally:
        db.close()


def start_background() -> None:
    interval = float(os.environ.get("ATTEMPT_EXPIRY_SWEEP_SECONDS", "30"))
    if interval <= 0:
        return

    def loop():
        stop = threading.Event()
        while not stop.wait(interval):
            try:
                n = finalize_expired()
                if n:
                    log.info("auto-submitted %d expired attempt(s)", n)
            except Exception:  # noqa: BLE001 - keep sweeping; the next pass retries
                log.exception("expiry sweep failed")

    threading.Thread(target=loop, name="attempt-expiry", daemon=True).start()
