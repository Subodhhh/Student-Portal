from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_student
from app.database import get_db
from app.schemas.test import TestResponse, TestStartResponse
from app.services.test_service import (
    get_available_tests,
    get_test,
    get_test_for_start,
)


router = APIRouter(
    prefix="/tests",
    tags=["Tests"],
)


@router.get(
    "",
    response_model=list[TestResponse],
)
def get_tests(
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_available_tests(
        db=db,
        student_id=student_id,
    )


@router.get(
    "/{test_id}/start",
    response_model=TestStartResponse,
)
def start_test(
    test_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    test = get_test_for_start(
        db=db,
        test_id=test_id,
        student_id=student_id,
    )

    if not test:
        raise HTTPException(
            status_code=404,
            detail="Test not found",
        )

    return test


@router.get(
    "/{test_id}",
    response_model=TestResponse,
)
def get_test_details(
    test_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    test = get_test(
        db=db,
        test_id=test_id,
        student_id=student_id,
    )

    if not test:
        raise HTTPException(
            status_code=404,
            detail="Test not found",
        )

    return test