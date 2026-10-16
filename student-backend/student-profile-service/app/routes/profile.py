from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_student_id
from app.database import SessionLocal
from app.schemas.profile import ProfileResponse, ProfileUpdateRequest
from app.services.profile import (
    get_student_profile,
    update_student_profile,
)

router = APIRouter(
    prefix="/profile",
    tags=["Student Profile"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=ProfileResponse)
def get_profile(
    student_id: str = Depends(get_current_student_id),
    db: Session = Depends(get_db),
):
    student = get_student_profile(
        db,
        student_id,
    )

    if not student:
        raise HTTPException(
            status_code=404,
            detail="Student profile not found",
        )

    return student


@router.put("", response_model=ProfileResponse)
def update_profile(
    profile_data: ProfileUpdateRequest,
    student_id: str = Depends(get_current_student_id),
    db: Session = Depends(get_db),
):
    student = update_student_profile(
        db,
        student_id,
        profile_data,
    )

    if not student:
        raise HTTPException(
            status_code=404,
            detail="Student profile not found",
        )

    return student