from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_student
from app.database import get_db
from app.repositories.attempt_answer_repository import (
    get_answers_by_attempt,
)
from app.repositories.attempt_repository import (
    get_attempt_by_test,
    get_completed_attempts,
)
from app.schemas.attempt import (
    AttemptResponse,
    AttemptSummaryResponse,
    AttemptWorkspaceResponse,
    StartAttemptRequest,
)
from app.schemas.attempt_answer import (
    AttemptAnswerResponse,
    SaveAnswerRequest,
)
from app.services.attempt_answer_service import (
    clear_response,
    mark_for_review,
    save_answer,
    visit_question,
)
from app.services.attempt_service import (
    get_attempt,
    get_attempt_summary,
    get_attempt_workspace,
    start_attempt,
    submit_attempt,
)


router = APIRouter(
    prefix="/attempts",
    tags=["Attempts"],
)


@router.post(
    "/start",
    response_model=AttemptResponse,
)
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

    return start_attempt(
        db=db,
        student_id=student_id,
        test_id=request.test_id,
        duration_minutes=test["duration_minutes"],
    )


@router.get(
    "/test/{test_id}",
    response_model=AttemptResponse,
)
def get_test_attempt_by_test(
    test_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    attempt = get_attempt_by_test(
        db=db,
        student_id=student_id,
        test_id=test_id,
    )

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="No attempt found",
        )

    return attempt


@router.get(
    "/completed",
    response_model=list[AttemptResponse],
)
def get_completed_test_attempts(
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_completed_attempts(
        db=db,
        student_id=student_id,
    )

@router.get(
    "/{attempt_id}/summary",
    response_model=AttemptSummaryResponse,
)
def get_attempt_summary_data(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_attempt_summary(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

@router.get(
    "/{attempt_id}/workspace",
    response_model=AttemptWorkspaceResponse,
)
def get_attempt_workspace_data(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_attempt_workspace(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )


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
    "/{attempt_id}/questions/{question_id}/visit",
    response_model=AttemptAnswerResponse,
)
def visit_test_question(
    attempt_id: UUID,
    question_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return visit_question(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
        question_id=question_id,
    )


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


@router.post(
    "/{attempt_id}/questions/{question_id}/clear",
    response_model=AttemptAnswerResponse,
)
def clear_test_response(
    attempt_id: UUID,
    question_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return clear_response(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
        question_id=question_id,
    )


@router.post(
    "/{attempt_id}/questions/{question_id}/mark-review",
    response_model=AttemptAnswerResponse,
)
def mark_test_question_for_review(
    attempt_id: UUID,
    question_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return mark_for_review(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
        question_id=question_id,
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