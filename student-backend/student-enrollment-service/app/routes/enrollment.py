from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_student_id
from app.database import SessionLocal
from app.schemas.enrollment import EnrollmentResponse
from app.services.enrollment_service import get_my_enrollments

router = APIRouter(
    prefix="/enrollments",
    tags=["Student Enrollments"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get(
    "",
    response_model=list[EnrollmentResponse],
)
def get_enrollments(
    student_id: str = Depends(get_current_student_id),
    db: Session = Depends(get_db),
):
    return get_my_enrollments(
        db=db,
        student_id=student_id,
    )