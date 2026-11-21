from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import question_types as qtypes
from app.models.attempt_answer import (
    AttemptAnswer,
    QuestionState,
)
from app.models.test_attempt import AttemptStatus
from app.repositories.attempt_answer_repository import (
    create_answer,
    get_answer,
    mark_question_visited,
    update_answer,
)
from app.repositories.attempt_repository import get_attempt_by_id
from app.services.paper_selection import SNAPSHOT_FILTER


def get_active_attempt_or_raise(
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
        attempt.status = AttemptStatus.AUTO_SUBMITTED
        attempt.submitted_at = now
        db.commit()

    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail="Attempt is no longer active",
        )

    return attempt


def validate_question(
    db: Session,
    test_id: UUID,
    question_id: UUID,
    attempt_id: UUID,
):
    # Global Test with paper choice: only the attempt's own papers (a question of another contribution "does not belong" to it)
    question = db.execute(
        text(f"""
            SELECT q.question_id, q.question_type, q.content
            FROM questions q
            WHERE q.question_id = :question_id
              AND q.test_id = :test_id
              AND {SNAPSHOT_FILTER}
        """),
        {
            "question_id": question_id,
            "test_id": test_id,
            "attempt_id": attempt_id,
        },
    ).first()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Question does not belong to this test",
        )
    return question


def canonical_answer(question, selected_answer: str | None) -> str | None:
    """The answer as stored, checked against the question's type with the shared rulebook (app/question_types.py): MCQ one of A-D,
    MSQ distinct letters of its options (stored sorted), NAT a plain number (stored canonical). Anything else is refused with 422
    and never stored, so it can never be scored (decision D12)."""
    qtype = question.question_type if question.question_type in (qtypes.MSQ, qtypes.NAT) else qtypes.MCQ
    content = question.content
    if isinstance(content, str):                       # SQLite (tests) returns JSON as text
        import json
        content = json.loads(content)
    try:
        return qtypes.normalise_answer(qtype, selected_answer, content=content)
    except qtypes.QuestionTypeError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_ANSWER", "message": exc.message})


def visit_question(
    db: Session,
    attempt_id: UUID,
    student_id: str,
    question_id: UUID,
) -> AttemptAnswer:

    attempt = get_active_attempt_or_raise(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    validate_question(
        db=db,
        test_id=attempt.test_id,
        question_id=question_id,
        attempt_id=attempt.attempt_id,
    )

    answer = get_answer(
        db=db,
        attempt_id=attempt_id,
        question_id=question_id,
    )

    if answer:
        return mark_question_visited(
            db=db,
            answer=answer,
        )

    now = datetime.now(timezone.utc)

    answer = AttemptAnswer(
        attempt_id=attempt_id,
        question_id=question_id,
        selected_answer=None,
        question_state=QuestionState.VISITED_UNANSWERED,
        visited_at=now,
        answered_at=None,
        updated_at=now,
    )

    return create_answer(
        db=db,
        answer=answer,
    )


def save_answer(
    db: Session,
    attempt_id: UUID,
    student_id: str,
    question_id: UUID,
    selected_answer: str | None,
) -> AttemptAnswer:

    attempt = get_active_attempt_or_raise(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    question = validate_question(
        db=db,
        test_id=attempt.test_id,
        question_id=question_id,
        attempt_id=attempt.attempt_id,
    )
    selected_answer = canonical_answer(question, selected_answer)

    answer = get_answer(
        db=db,
        attempt_id=attempt_id,
        question_id=question_id,
    )

    question_state = (
        QuestionState.ANSWERED
        if selected_answer is not None
        else QuestionState.VISITED_UNANSWERED
    )

    if answer:
        return update_answer(
            db=db,
            answer=answer,
            selected_answer=selected_answer,
            question_state=question_state,
        )

    now = datetime.now(timezone.utc)

    answer = AttemptAnswer(
        attempt_id=attempt_id,
        question_id=question_id,
        selected_answer=selected_answer,
        question_state=question_state,
        visited_at=now,
        answered_at=now if selected_answer else None,
        updated_at=now,
    )

    return create_answer(
        db=db,
        answer=answer,
    )


def clear_response(
    db: Session,
    attempt_id: UUID,
    student_id: str,
    question_id: UUID,
) -> AttemptAnswer:

    return save_answer(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
        question_id=question_id,
        selected_answer=None,
    )


def mark_for_review(
    db: Session,
    attempt_id: UUID,
    student_id: str,
    question_id: UUID,
) -> AttemptAnswer:

    attempt = get_active_attempt_or_raise(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    validate_question(
        db=db,
        test_id=attempt.test_id,
        question_id=question_id,
        attempt_id=attempt.attempt_id,
    )

    answer = get_answer(
        db=db,
        attempt_id=attempt_id,
        question_id=question_id,
    )

    if answer:
        return update_answer(
            db=db,
            answer=answer,
            selected_answer=answer.selected_answer,
            question_state=QuestionState.MARKED_FOR_REVIEW,
        )

    now = datetime.now(timezone.utc)

    answer = AttemptAnswer(
        attempt_id=attempt_id,
        question_id=question_id,
        selected_answer=None,
        question_state=QuestionState.MARKED_FOR_REVIEW,
        visited_at=now,
        answered_at=None,
        updated_at=now,
    )

    return create_answer(
        db=db,
        answer=answer,
    )