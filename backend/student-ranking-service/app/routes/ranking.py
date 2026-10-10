from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import get_current_student
from app.database import get_db
from app.schemas.ranking import RankingResponse
from app.services.ranking_service import get_ranking


router = APIRouter(
    prefix="/ranking",
    tags=["Ranking"],
)


@router.get(
    "/{test_id}",
    response_model=RankingResponse,
)
def get_test_ranking(
    test_id: UUID,
    student_id: str = Depends(get_current_student),
    db=Depends(get_db),
):
    return get_ranking(
        db=db,
        test_id=test_id,
        student_id=student_id,
    )