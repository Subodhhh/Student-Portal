from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_student
from app.database import get_db
from app.repositories.attempt_answer_repository import get_answers_by_attempt
from app.schemas.attempt import AttemptResponse, StartAttemptRequest
from app.schemas.attempt_answer import (
    AttemptAnswerResponse,
    SaveAnswerRequest,
)
from app.services.attempt_answer_service import save_answer
from app.services.attempt_service import (
    get_attempt,
    start_attempt,
    submit_attempt,
)


router = APIRouter(prefix="/attempts", tags=["Attempts"])


@router.post("/start", response_model=AttemptResponse)
def start_test_attempt(
    request: StartAttemptRequest,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    test = db.execute(
        text("""
            SELECT test_id, duration_minutes, status
            FROM tests
            WHERE test_id = :test_id
        """),
        {"test_id": request.test_id},
    ).mappings().first()

    if not test:
        raise HTTPException(
            status_code=404,
            detail="Test not found",
        )

    if test["status"] != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail="Test is not active",
        )

    eligible = db.execute(
        text("""
            SELECT 1
            FROM test_audiences ta
            LEFT JOIN enrollments e
                ON e.student_id = :student_id
               AND e.status = 'ACTIVE'
            LEFT JOIN enrollment_batches eb
                ON eb.enrollment_id = e.enrollment_id
            WHERE ta.test_id = :test_id
              AND (
                    (
                        ta.audience_type = 'PROVIDER'
                        AND ta.provider_id = e.provider_id
                    )
                    OR
                    (
                        ta.audience_type = 'BRANCH'
                        AND ta.provider_id = e.provider_id
                        AND ta.branch_id = e.branch_id
                    )
                    OR
                    (
                        ta.audience_type = 'BATCH'
                        AND ta.provider_id = e.provider_id
                        AND ta.batch_id = eb.batch_id
                    )
                    OR
                    (
                        ta.audience_type = 'STUDENT'
                        AND ta.student_id = :student_id
                    )
                  )
            LIMIT 1
        """),
        {
            "test_id": request.test_id,
            "student_id": student_id,
        },
    ).first()

    if not eligible:
        raise HTTPException(
            status_code=403,
            detail="You are not eligible to attempt this test",
        )

    attempt = start_attempt(
        db=db,
        student_id=student_id,
        test_id=request.test_id,
        duration_minutes=test["duration_minutes"],
    )

    return attempt


@router.get(
    "/{attempt_id}",
    response_model=AttemptResponse,
)
def get_test_attempt(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
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

    return attempt


@router.post(
    "/{attempt_id}/answers",
    response_model=AttemptAnswerResponse,
)
def save_test_answer(
    attempt_id: UUID,
    request: SaveAnswerRequest,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return save_answer(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
        question_id=request.question_id,
        selected_answer=request.selected_answer,
    )


@router.get(
    "/{attempt_id}/answers",
    response_model=list[AttemptAnswerResponse],
)
def get_test_answers(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
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

    return get_answers_by_attempt(
        db=db,
        attempt_id=attempt_id,
    )


@router.post(
    "/{attempt_id}/submit",
    response_model=AttemptResponse,
)
def submit_test_attempt(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return submit_attempt(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )