"""Global Test with paper choice paper selection (one test per occurrence, several papers per subject).

The test service owns each student's eligibility (frozen at generation, never changed after delivery) and is read through its internal
API. This service owns the student's choice (paper_selections) and the attempt's snapshot (attempt_selected_papers).

Locking (one local transaction each, no cross-service lock): saving a choice and starting an attempt both begin by locking the
student's paper_selections rows for the test (SELECT ... FOR UPDATE).
  * A save then refuses (409) once an ordinary attempt exists - checked only AFTER taking the lock, so a save waiting on a Start sees
    the committed attempt.
  * A Start builds the snapshot ONLY from the rows returned by that locking query: it waits for a concurrent save and gets the newest
    committed value; a choice inserted meanwhile for a subject without a row is not returned, so that subject counts as missing and
    the Start is refused (SELECTION_REQUIRED) - never a stale or incomplete snapshot.
  * The attempt and its snapshot rows commit together; the partial unique index on (student, test) admits one ordinary attempt.
Tests without paper choice (no papers in tests.sections) never reach this module: their attempts have no snapshot rows."""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.paper_selection import AttemptSelectedPaper, PaperSelection
from app.models.test_attempt import TestAttempt

# The questions an attempt may see, answer and be scored on: its snapshot's papers; every question when it has no snapshot (all
# attempts without paper choice). Needs :attempt_id and a questions alias q.
SNAPSHOT_FILTER = """(NOT EXISTS (SELECT 1 FROM attempt_selected_papers sp WHERE sp.attempt_id = :attempt_id)
                      OR q.paper_id IN (SELECT sp.paper_id FROM attempt_selected_papers sp WHERE sp.attempt_id = :attempt_id))"""


def has_paper_choice(sections) -> bool:
    if isinstance(sections, str):
        sections = json.loads(sections)
    return bool(sections) and all("papers" in s for s in sections)


def stored_sections(db: Session, test_id):
    return db.execute(text("SELECT sections FROM tests WHERE test_id = :t"), {"t": test_id}).scalar()


def fetch_eligibility(test_id, student_id: str) -> list[dict]:
    """The student's subjects (position, subject, status, message, papers) from the test service. 404 when the student is not in the
    test; 503 when the test service cannot be reached (nothing is written, the student can retry)."""
    if not settings.student_test_service_url or not settings.student_test_service_api_key:
        raise HTTPException(status_code=503, detail="Paper selection is temporarily unavailable")
    url = (settings.student_test_service_url.rstrip("/") + f"/internal/v1/tests/{test_id}/eligibility/"
           + urllib.parse.quote(student_id, safe=""))
    request = urllib.request.Request(url, headers={"X-Service-Key": settings.student_test_service_api_key, "X-Service-Name": "student-attempt-service"})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(status_code=404, detail="Test not found")
        raise HTTPException(status_code=503, detail="Paper selection is temporarily unavailable")
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise HTTPException(status_code=503, detail="Paper selection is temporarily unavailable")
    if not body.get("paper_selection"):
        raise HTTPException(status_code=404, detail="This test has no paper choice")
    return body["subjects"]


def _lock(db: Session, test_id, student_id: str) -> dict[str, PaperSelection]:
    rows = db.scalars(select(PaperSelection).where(PaperSelection.test_id == test_id, PaperSelection.student_id == student_id)
                      .with_for_update()).all()
    return {r.subject: r for r in rows}


def _ordinary_attempt(db: Session, test_id, student_id: str) -> TestAttempt | None:
    return db.scalar(select(TestAttempt).where(TestAttempt.test_id == test_id, TestAttempt.student_id == student_id,
                                               TestAttempt.retake_of_attempt_id.is_(None)))


def snapshot_of(db: Session, attempt_id) -> dict[str, UUID]:
    return {r.subject: r.paper_id for r in db.scalars(select(AttemptSelectedPaper).where(AttemptSelectedPaper.attempt_id == attempt_id))}


def view(db: Session, test_id, student_id: str, subjects: list[dict]) -> dict:
    """Eligible papers per subject with the current choice. Once an attempt exists it is locked and shows the attempt's papers."""
    current = db.scalar(select(TestAttempt).where(TestAttempt.test_id == test_id, TestAttempt.student_id == student_id)
                        .order_by(TestAttempt.started_at.desc()).limit(1))
    if current:
        chosen = {s: str(p) for s, p in snapshot_of(db, current.attempt_id).items()}
    else:
        chosen = {r.subject: str(r.paper_id) for r in db.scalars(select(PaperSelection).where(
            PaperSelection.test_id == test_id, PaperSelection.student_id == student_id))}
    out = []
    for s in subjects:
        papers = [str(p["paper_id"]) for p in s["papers"]]
        selected = chosen.get(s["subject"]) if chosen.get(s["subject"]) in papers else None
        if selected is None and s["status"] == "PAPER" and len(papers) == 1:
            selected = papers[0]                        # exactly one eligible paper: included without a choice
        out.append({**s, "selected_paper_id": selected})
    complete = all(x["selected_paper_id"] for x in out if x["status"] == "SELECTABLE")
    return {"test_id": str(test_id), "locked": current is not None, "complete": complete,
            "attempt_id": str(current.attempt_id) if current else None, "subjects": out}


def save(db: Session, test_id, student_id: str, subject: str, paper_id: UUID, subjects: list[dict]) -> None:
    cell = next((s for s in subjects if s["subject"] == subject), None)
    if not cell or cell["status"] != "SELECTABLE":
        raise HTTPException(status_code=422, detail={"code": "SELECTION_NOT_REQUIRED", "message": "This subject has no paper to choose."})
    if str(paper_id) not in {str(p["paper_id"]) for p in cell["papers"]}:
        raise HTTPException(status_code=422, detail={"code": "PAPER_NOT_ELIGIBLE", "message": "This paper is not available to you."})
    locked = _lock(db, test_id, student_id)
    if _ordinary_attempt(db, test_id, student_id):       # after the lock: a Start that won is visible here
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "SELECTION_LOCKED", "message": "Your papers are fixed once the test has started."})
    row = locked.get(subject)
    if row:
        if row.paper_id != paper_id:
            row.paper_id, row.updated_at = paper_id, datetime.now(timezone.utc)
    else:
        db.add(PaperSelection(test_id=test_id, student_id=student_id, subject=subject, paper_id=paper_id))
    db.commit()


def lock_snapshot(db: Session, test_id, student_id: str, subjects: list[dict]) -> dict[str, UUID]:
    """Inside the Start transaction: lock the choices and return the attempt's papers per subject, or refuse. The caller writes them
    with the attempt and commits once."""
    locked = _lock(db, test_id, student_id)
    papers, missing = {}, []
    for s in subjects:
        eligible = {str(p["paper_id"]) for p in s["papers"]}
        if s["status"] == "PAPER" and len(eligible) == 1:
            papers[s["subject"]] = UUID(next(iter(eligible)))
        elif s["status"] == "SELECTABLE":
            row = locked.get(s["subject"])
            if row is None or str(row.paper_id) not in eligible:
                missing.append(s["subject"])
            else:
                papers[s["subject"]] = row.paper_id
    if missing:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "SELECTION_REQUIRED", "subjects": missing,
                                                     "message": "Choose a paper for every subject before starting."})
    if not papers:
        db.rollback()
        raise HTTPException(status_code=400, detail="No paper is available to you in this test")
    return papers


def copy_snapshot(db: Session, original_attempt_id, retake_attempt_id) -> None:
    """A retake sits exactly the original's papers (no new choice); a no-op for attempts without a snapshot."""
    for subject, paper_id in snapshot_of(db, original_attempt_id).items():
        db.add(AttemptSelectedPaper(attempt_id=retake_attempt_id, subject=subject, paper_id=paper_id))
