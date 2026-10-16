"""Automatic evaluation of every submitted attempt, without depending on the student's browser.

The frontend still asks for evaluation right after submitting (instant feedback); this sweep covers everything else: auto-submitted
attempts whose browser was closed, interrupted requests, and service restarts. Evaluation is idempotent (unique attempt_id), so
running it concurrently with a request or from several processes never creates a second result."""
import logging
import os
import threading

from fastapi import HTTPException

from app.database import SessionLocal
from app.repositories.result_repository import get_unevaluated_attempts
from app.services.result_service import evaluate_attempt

log = logging.getLogger("result.auto_evaluation")


def evaluate_pending(limit: int = 100) -> dict:
    db = SessionLocal()
    stats = {"evaluated": 0, "failed": 0}
    try:
        for row in get_unevaluated_attempts(db, limit=limit):
            try:
                evaluate_attempt(db=db, attempt_id=row["attempt_id"], student_id=row["student_id"])
                stats["evaluated"] += 1
            except HTTPException as exc:
                db.rollback()
                stats["failed"] += 1
                log.warning("attempt %s could not be evaluated: %s", row["attempt_id"], exc.detail)
        return stats
    finally:
        db.close()


def start_background() -> None:
    interval = float(os.environ.get("AUTO_EVALUATION_INTERVAL_SECONDS", "20"))
    if interval <= 0:
        return

    def loop():
        stop = threading.Event()
        while not stop.wait(interval):
            try:
                stats = evaluate_pending()
                if stats["evaluated"] or stats["failed"]:
                    log.info("auto evaluation: %s", stats)
            except Exception:  # noqa: BLE001 - keep sweeping; the next pass retries
                log.exception("auto evaluation pass failed")

    threading.Thread(target=loop, name="auto-evaluation", daemon=True).start()
