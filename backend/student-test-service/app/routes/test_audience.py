from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.internal import require_service_key
from app.schemas.test_audience import (
    TestAudienceCreate,
    TestAudienceResponse,
)
from app.services.test_audience_service import (
    add_test_audience,
    get_test_audiences,
)

# Who may take a test is an administrative decision: these routes are for trusted services only (the same scoped service key
# as /internal/v1). A student session must never be able to add itself (or anyone) to a test's audience or list it.
router = APIRouter(
    prefix="/tests",
    tags=["Test Audiences"],
    dependencies=[Depends(require_service_key)],
)


@router.post(
    "/{test_id}/audiences",
    response_model=TestAudienceResponse,
)
def create_test_audience(
    test_id: UUID,
    audience_data: TestAudienceCreate,
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
    db: Session = Depends(get_db),
):
    return get_test_audiences(
        db=db,
        test_id=test_id,
    )