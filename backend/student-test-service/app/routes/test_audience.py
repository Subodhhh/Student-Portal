from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_student
from app.database import get_db
from app.schemas.test_audience import (
    TestAudienceCreate,
    TestAudienceResponse,
)
from app.services.test_audience_service import (
    add_test_audience,
    get_test_audiences,
)

router = APIRouter(
    prefix="/tests",
    tags=["Test Audiences"],
)


@router.post(
    "/{test_id}/audiences",
    response_model=TestAudienceResponse,
)
def create_test_audience(
    test_id: UUID,
    audience_data: TestAudienceCreate,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return add_test_audience(
        db=db,
        test_id=test_id,
        audience_data=audience_data,
    )


@router.get(
    "/{test_id}/audiences",
    response_model=list[TestAudienceResponse],
)
def get_audiences(
    test_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_test_audiences(
        db=db,
        test_id=test_id,
    )