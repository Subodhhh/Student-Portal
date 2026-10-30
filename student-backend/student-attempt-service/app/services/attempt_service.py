from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.attempt_answer import QuestionState
from app.models.test_attempt import AttemptStatus, TestAttempt
from app.repositories.attempt_answer_repository import (
    get_answers_by_attempt,
)
from app.repositories.attempt_repository import (
    create_attempt,
    get_attempt_by_id,
    get_attempt_by_test,
)
from app.models.paper_selection import AttemptSelectedPaper
from app.services import paper_selection
from app.services.integrity import finish, start_retake
from app.services.question_order import section_order


def start_attempt(
    db: Session,
    student_id: str,
    test_id: UUID,
    duration_minutes: int,
    _retry: bool = True,
):
    existing_attempt = get_attempt_by_test(
        db=db,
        student_id=student_id,
        test_id=test_id,
    )

    if existing_attempt:
        if existing_attempt.status == AttemptStatus.IN_PROGRESS:
            now = datetime.now(timezone.utc)

            expires_at = existing_attempt.expires_at

            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(
                    tzinfo=timezone.utc
                )

            if now >= expires_at:
                finish(existing_attempt, now, "TIME_EXPIRED")

                db.commit()
                db.refresh(existing_attempt)

                raise HTTPException(
                    status_code=400,
                    detail="Test attempt has expired",
                )

            return existing_attempt

        # a provider authorized a retake of an attempt submitted automatically after warnings: a NEW linked attempt
        retake = start_retake(db, existing_attempt, duration_minutes)
        if retake is not None:
            return retake

        raise HTTPException(
            status_code=400,
            detail="You have already attempted this test",
        )

    started_at = datetime.now(timezone.utc)

    expires_at = started_at + timedelta(
        minutes=duration_minutes
    )

    attempt = TestAttempt(
        test_id=test_id,
        student_id=student_id,
        started_at=started_at,
        expires_at=expires_at,
        status=AttemptStatus.IN_PROGRESS,
    )

    try:
        if paper_selection.has_paper_choice(paper_selection.stored_sections(db, test_id)):
            # Global Test with paper choice: the attempt and its papers in ONE transaction (see services/paper_selection)
            subjects = paper_selection.fetch_eligibility(test_id, student_id)
            papers = paper_selection.lock_snapshot(db, test_id, student_id, subjects)
            db.add(attempt)
            db.flush()
            for subject, paper_id in papers.items():
                db.add(AttemptSelectedPaper(attempt_id=attempt.attempt_id, subject=subject, paper_id=paper_id))
            db.commit()
            db.refresh(attempt)
            return attempt

        return create_attempt(
            db=db,
            attempt=attempt,
        )

    except IntegrityError:
        db.rollback()

        if _retry:
            # a concurrent Start created the attempt first: return that attempt (the resume path above), never a second one
            return start_attempt(db=db, student_id=student_id, test_id=test_id, duration_minutes=duration_minutes, _retry=False)

        raise HTTPException(
            status_code=400,
            detail="You have already attempted this test",
        )


def get_attempt(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    attempt = get_attempt_by_id(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not attempt:
        return None

    now = datetime.now(timezone.utc)

    expires_at = attempt.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    if (
        attempt.status == AttemptStatus.IN_PROGRESS
        and now >= expires_at
    ):
        finish(attempt, now, "TIME_EXPIRED")

        db.commit()
        db.refresh(attempt)

    return attempt


def get_attempt_workspace(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    attempt = get_attempt(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Attempt not found",
        )

    rows = db.execute(
        text(f"""
            SELECT
                q.question_id,
                q.question_order,
                q.subject,
                q.question_text,
                q.question_type,
                q.option_a,
                q.option_b,
                q.option_c,
                q.option_d,
                q.marks,
                q.negative_marks,
                q.image_reference,
                aa.selected_answer,
                aa.question_state,
                aa.updated_at
            FROM questions q
            LEFT JOIN attempt_answers aa
                ON aa.question_id = q.question_id
               AND aa.attempt_id = :attempt_id
            WHERE q.test_id = :test_id
              AND {paper_selection.SNAPSHOT_FILTER}
            ORDER BY
                q.subject ASC NULLS LAST,
                q.question_order ASC
        """),
        {
            "attempt_id": attempt_id,
            "test_id": attempt.test_id,
        },
    ).mappings().all()

    sections = db.execute(text("SELECT sections FROM tests WHERE test_id = :t"), {"t": attempt.test_id}).scalar()
    if isinstance(sections, str):
        import json
        sections = json.loads(sections)
    rows = section_order(rows, sections, student_id, attempt.test_id, subject_of=lambda r: r["subject"], id_of=lambda r: r["question_id"])

    now = datetime.now(timezone.utc)

    expires_at = attempt.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    remaining_seconds = max(
        0,
        int(
            (expires_at - now).total_seconds()
        ),
    )

    current_question_id = None

    for row in rows:
        if row["question_state"] is not None:
            current_question_id = row["question_id"]

    questions = []

    for row in rows:
        questions.append(
            {
                "question_id": row["question_id"],
                "question_order": row["question_order"],
                "subject": row["subject"],
                "question_text": row["question_text"],
                "question_type": row["question_type"],
                "option_a": row["option_a"],
                "option_b": row["option_b"],
                "option_c": row["option_c"],
                "option_d": row["option_d"],
                "marks": row["marks"],
                "negative_marks": row["negative_marks"],
                "image_reference": row["image_reference"],
                "selected_answer": row["selected_answer"],
                "question_state": (
                    row["question_state"]
                    or QuestionState.NOT_VISITED.value
                ),
            }
        )

    return {
        "attempt_id": attempt.attempt_id,
        "test_id": attempt.test_id,
        "started_at": attempt.started_at,
        "expires_at": attempt.expires_at,
        "status": attempt.status.value,
        "remaining_seconds": remaining_seconds,
        "current_question_id": current_question_id,
        "questions": questions,
    }

def get_attempt_summary(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    attempt = get_attempt_by_id(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Attempt not found",
        )

    summary = db.execute(
        text(f"""
            SELECT
                COUNT(q.question_id) AS total_questions,

                COUNT(
                    CASE
                        WHEN aa.question_state = 'ANSWERED'
                        THEN 1
                    END
                ) AS answered,

                COUNT(
                    CASE
                        WHEN aa.question_state = 'VISITED_UNANSWERED'
                        THEN 1
                    END
                ) AS not_answered,

                COUNT(
                    CASE
                        WHEN aa.question_state = 'MARKED_FOR_REVIEW'
                        THEN 1
                    END
                ) AS marked_for_review

            FROM questions q

            LEFT JOIN attempt_answers aa
                ON aa.question_id = q.question_id
               AND aa.attempt_id = :attempt_id

            WHERE q.test_id = :test_id
              AND {paper_selection.SNAPSHOT_FILTER}
        """),
        {
            "attempt_id": attempt_id,
            "test_id": attempt.test_id,
        },
    ).mappings().one()

    total_questions = summary["total_questions"]

    answered = summary["answered"]
    not_answered = summary["not_answered"]
    marked_for_review = summary["marked_for_review"]

    not_visited = (
        total_questions
        - answered
        - not_answered
        - marked_for_review
    )

    return {
        "attempt_id": attempt.attempt_id,
        "test_id": attempt.test_id,
        "total_questions": total_questions,
        "answered": answered,
        "not_answered": not_answered,
        "marked_for_review": marked_for_review,
        "not_visited": not_visited,
    }

def submit_attempt(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    attempt = get_attempt_by_id(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Attempt not found",
        )

    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail="Attempt is no longer active",
        )

    now = datetime.now(timezone.utc)

    expires_at = attempt.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    finish(attempt, now, "TIME_EXPIRED" if now >= expires_at else "STUDENT")

    db.commit()
    db.refresh(attempt)

    return attempt