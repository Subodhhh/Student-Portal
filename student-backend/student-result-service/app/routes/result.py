from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import get_current_student
from app.database import get_db
from app.schemas.result import (
    ResultListItem,
    ResultResponse,
    ResultDetailsResponse,
)
from app.services.result_service import (
    evaluate_attempt,
    get_result,
    get_results_for_student,
    get_result_details,
)


router = APIRouter(
    prefix="/results",
    tags=["Results"],
)


@router.get(
    "",
    response_model=list[ResultListItem],
)
def get_student_results(
    student_id: str = Depends(get_current_student),
    db=Depends(get_db),
):
    return get_results_for_student(
        db=db,
        student_id=student_id,
    )


@router.post(
    "/attempts/{attempt_id}/evaluate",
    response_model=ResultResponse,
)
def evaluate_test_attempt(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db=Depends(get_db),
):
    return evaluate_attempt(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )


@router.get(
    "/attempts/{attempt_id}",
    response_model=ResultResponse,
)
def get_test_result(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db=Depends(get_db),
):
    return get_result(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

@router.get(
    "/attempts/{attempt_id}/details",
    response_model=ResultDetailsResponse,
)
def get_test_result_details(
    attempt_id: UUID,
    student_id: str = Depends(get_current_student),
    db=Depends(get_db),
):
    return get_result_details(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )