"""Keeps the Student database's read projection of Institute-owned reference data current.

The Institute database is the source of truth for providers, branches, batches, enrollments and batch membership. The Student
services (eligibility, "My Institutes", result listings, the foreign keys of delivered tests) need those rows in their own
database, so this job pushes the current state to the Student enrollment service's authenticated internal API.

State-based and idempotent: every pass computes a digest of each row and sends only rows whose digest changed since they were
last accepted (everything once after a restart). Rows the receiver rejects are retried on the next pass and reported in
`status()`, which the Service health page shows. Batch membership travels with its enrollment as a complete set, so removals
propagate without tombstones. Safe to run from several processes at once: the receiver applies one sync at a time and answers 409
to the others.

Small requests with no automatic retry: a request the receiver is still working on is never sent again on top of it (that queued
duplicate upserts on the same rows). A 409, a timeout or a transport error ends the pass as deferred; rows already accepted stay
accepted and the next pass sends the rest. Rows the receiver rejected, unchanged since, go last and wait REJECTED_RETRY between
attempts, so they never hold up valid rows; they stay listed in status() until accepted."""
import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.database import new_session
from institute_common.internal_http import InternalHttp, InternalServiceError
from institute_common.models.org import Batch, Branch, Enrollment, EnrollmentBatch, Provider

log = logging.getLogger("institute.reference_sync")

CHUNK = 25
TIMEOUT = 120.0                 # a 25-row request takes ~30 s against a remote database
REJECTED_RETRY = 1800.0         # seconds between attempts at an unchanged rejected row (a retry round costs the receiver minutes)
KINDS = ("providers", "branches", "batches", "enrollments")
SINGULAR = {"providers": "provider", "branches": "branch", "batches": "batch", "enrollments": "enrollment"}
ID_FIELD = {"providers": "provider_id", "branches": "branch_id", "batches": "batch_id", "enrollments": "enrollment_id"}
_accepted: dict[tuple[str, str], str] = {}     # (kind, id) -> digest last accepted by the receiver
_rejected: dict[tuple[str, str], tuple[str, float, dict]] = {}   # (kind, id) -> (digest, when, the receiver's error)
_lock = threading.Lock()
_running = threading.Lock()                    # one pass at a time in this process (the loop and sync-now)
_status: dict = {"last_run": None, "last_success": None, "last_error": None, "rejected": [], "sent": 0}
_client_override: InternalHttp | None = None   # tests


def set_client(client: InternalHttp | None) -> None:
    global _client_override
    _client_override = client


def reset_cache() -> None:
    with _lock:
        _accepted.clear()
        _rejected.clear()


def status() -> dict:
    with _lock:
        return {**_status, "rejected": [err for _, _, err in _rejected.values()][:20], "configured": _configured()}


def _configured() -> bool:
    return _client_override is not None or bool(settings.student_enrollment_service_url and settings.student_enrollment_service_api_key)


def _iso(v):
    return v.isoformat() if v is not None else None


def snapshot(db: Session) -> dict[str, dict[str, dict]]:
    """Current state of every reference row, keyed by kind then id (JSON-ready values)."""
    out: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
    for p in db.scalars(select(Provider)):
        out["providers"][str(p.provider_id)] = {
            "provider_id": str(p.provider_id), "name": p.name, "email": p.email, "phone": p.phone, "address": p.address,
            "status": p.status, "deleted_at": _iso(p.deleted_at)}
    for b in db.scalars(select(Branch)):
        out["branches"][str(b.branch_id)] = {
            "branch_id": str(b.branch_id), "provider_id": str(b.provider_id), "name": b.name, "address": b.address, "city": b.city,
            "state": b.state, "pincode": b.pincode, "status": b.status, "deleted_at": _iso(b.deleted_at)}
    for b in db.scalars(select(Batch)):
        out["batches"][str(b.batch_id)] = {
            "batch_id": str(b.batch_id), "branch_id": str(b.branch_id), "name": b.name, "description": b.description,
            "start_date": _iso(b.start_date), "end_date": _iso(b.end_date), "status": b.status}
    members: dict[str, list[dict]] = defaultdict(list)
    for m in db.scalars(select(EnrollmentBatch).order_by(EnrollmentBatch.batch_id)):
        members[m.enrollment_id].append({"enrollment_batch_id": str(m.enrollment_batch_id), "batch_id": str(m.batch_id)})
    for e in db.scalars(select(Enrollment)):
        out["enrollments"][e.enrollment_id] = {
            "enrollment_id": e.enrollment_id, "provider_id": str(e.provider_id), "branch_id": str(e.branch_id),
            "student_id": e.student_id, "status": e.status, "enrolled_at": _iso(e.enrolled_at), "batches": members.get(e.enrollment_id, [])}
    return out


def _digest(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def _client() -> InternalHttp:
    if _client_override is not None:
        return _client_override
    return InternalHttp(settings.student_enrollment_service_url, settings.student_enrollment_service_api_key, timeout=TIMEOUT, retries=1)


def _clock() -> float:
    return time.monotonic()


def run_once(db: Session | None = None, *, full: bool = False) -> dict:
    """Push every changed reference row. Returns counts; never raises for receiver/transport problems (they are reported)."""
    if not _running.acquire(blocking=False):
        return {"pending": 0, "sent": 0, "rejected": 0, "skipped": "already_running"}
    try:
        return _run(db, full)
    finally:
        _running.release()


def _pending(rows: dict[str, dict], kind: str, full: bool, now: float) -> list[dict]:
    """Changed rows, those never rejected first; an unchanged rejected row only once REJECTED_RETRY has passed."""
    fresh, retry = [], []
    for rid, row in rows.items():
        digest = _digest(row)
        if not full and _accepted.get((kind, rid)) == digest:
            continue
        rejected = _rejected.get((kind, rid))
        if rejected and rejected[0] == digest:
            if full or now - rejected[1] >= REJECTED_RETRY:
                retry.append(row)
        else:
            fresh.append(row)
    return fresh + retry


def _run(db: Session | None, full: bool) -> dict:
    own = db is None
    db = db or new_session()
    now = datetime.now(timezone.utc).isoformat()
    try:
        if not _configured():
            with _lock:
                _status.update(last_run=now, last_error="Student enrollment service is not configured")
            return {"sent": 0, "rejected": 0, "error": "not_configured"}
        snap = snapshot(db)
    finally:
        if own:
            db.close()

    with _lock:
        for key in [k for k in _rejected if k[1] not in snap[k[0]]]:      # gone from the Institute: nothing left to report
            del _rejected[key]
        pending = {k: _pending(snap[k], k, full, _clock()) for k in KINDS}
    total = sum(len(v) for v in pending.values())
    sent, rejected = 0, 0
    try:
        client = _client()
        # Parents before children: providers, branches, batches, then enrollments (their membership references batches).
        for kind in KINDS:
            rows = pending[kind]
            for i in range(0, len(rows), CHUNK):
                chunk = rows[i:i + CHUNK]
                r = client.request("POST", "/reference/sync", json={kind: chunk})
                if r.status_code == 409:
                    with _lock:
                        _status.update(last_run=now, last_error="receiver busy: deferred to the next pass")
                    log.info("reference sync deferred: the receiver is busy with another sync")
                    return {"pending": total, "sent": sent, "rejected": rejected, "deferred": "busy"}
                if r.status_code != 200:
                    raise InternalServiceError(f"receiver answered {r.status_code}")
                errors = {(e["kind"], e["id"]): e for e in r.json().get("errors", [])}
                with _lock:
                    for row in chunk:
                        rid = row[ID_FIELD[kind]]
                        err = errors.get((SINGULAR[kind], rid))
                        if err:
                            _rejected[(kind, rid)] = (_digest(row), _clock(), err)
                            rejected += 1
                        else:
                            _accepted[(kind, rid)] = _digest(row)
                            _rejected.pop((kind, rid), None)
                            sent += 1
        with _lock:
            _status.update(last_run=now, last_success=now, last_error=None, sent=_status["sent"] + sent)
        if rejected:
            log.warning("reference sync: %d row(s) rejected by the Student service", rejected)
        return {"pending": total, "sent": sent, "rejected": rejected}
    except (InternalServiceError, ValueError, KeyError) as exc:
        # A timeout lands here too: deferred, never resent within this pass (the receiver may still be applying it).
        with _lock:
            _status.update(last_run=now, last_error=str(exc)[:200])
        log.warning("reference sync failed: %s", exc)
        return {"pending": total, "sent": sent, "rejected": rejected, "error": str(exc)[:200], "deferred": "error"}


_trigger = threading.Event()


def request_sync() -> None:
    """Ask the background loop for an immediate pass (after an enrollment change); never blocks the request."""
    _trigger.set()


def start_background() -> None:
    interval = settings.reference_sync_interval_seconds
    if interval <= 0:
        return

    def loop():
        from institute_common.context import service_context
        while True:
            try:
                with service_context("enrollment"):
                    run_once()
            except Exception:  # noqa: BLE001 - the loop must survive anything; the error is logged and shown in status()
                log.exception("reference sync pass crashed")
            _trigger.wait(timeout=interval)
            _trigger.clear()

    threading.Thread(target=loop, name="reference-sync", daemon=True).start()
