from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import get_current_student
from app.database import get_db
from app.models.question import Question, QuestionImage, QuestionPicture
from app.repositories.test_repository import get_test_for_student
from app.schemas.test import TestResponse, TestStartResponse
from app.services.test_service import (
    AttemptRequired,
    get_available_tests,
    get_missed_tests_for_student,
    get_scheduled_tests_for_student,
    get_test,
    get_test_for_start,
)
from app.services.test_service import (
    get_test_details as get_test_details_service,
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
    "/scheduled",
    response_model=list[TestResponse],
)
def get_scheduled_tests(
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_scheduled_tests_for_student(
        db=db,
        student_id=student_id,
    )


@router.get(
    "/missed",
    response_model=list[TestResponse],
)
def get_missed_tests_route(
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_missed_tests_for_student(
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
    try:
        test = get_test_for_start(
            db=db,
            test_id=test_id,
            student_id=student_id,
        )
    except AttemptRequired:
        raise HTTPException(
            status_code=409,
            detail={"code": "ATTEMPT_REQUIRED", "message": "Start the attempt first: its papers decide the questions."},
        )

    if not test:
        raise HTTPException(
            status_code=404,
            detail="Test not found",
        )

    return test


@router.get(
    "/{test_id}/details",
    response_model=TestResponse,
)
def get_test_details_route(
    test_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    test = get_test_details_service(
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
def get_test_by_id_route(
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

PICTURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, max-age=86400, immutable",     # a picture's bytes never change (it is named by its checksum)
}


@router.get("/{test_id}/images/{checksum}")
def question_picture(test_id: UUID, checksum: str, student_id: str = Depends(get_current_student), db: Session = Depends(get_db)):
    """A picture of a question or option, for a student who may see this test (its audience, during or after it), and only when one
    of this test's questions uses it. Every other request gets the same 404, so pictures of other tests cannot be probed."""
    used = db.scalar(select(Question.question_id).where(Question.test_id == test_id, or_(
        Question.question_image == checksum, Question.option_a_image == checksum, Question.option_b_image == checksum,
        Question.option_c_image == checksum, Question.option_d_image == checksum)).limit(1)) or db.scalar(
        select(QuestionPicture.question_id).join(Question, Question.question_id == QuestionPicture.question_id)
        .where(Question.test_id == test_id, QuestionPicture.checksum == checksum).limit(1))     # MSQ option pictures
    picture = db.get(QuestionImage, checksum) if used else None
    if picture is None or not get_test_for_student(db=db, test_id=test_id, student_id=student_id):
        raise HTTPException(status_code=404, detail="Picture not found")
    return Response(picture.image_data, media_type=picture.mime_type, headers=PICTURE_HEADERS)
