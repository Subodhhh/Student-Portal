from sqlalchemy.orm import Session

from app.models.student import Student
from app.schemas.profile import ProfileUpdateRequest


def get_student_profile(
    db: Session,
    student_id: str,
) -> Student | None:
    return (
        db.query(Student)
        .filter(
            Student.student_id == student_id,
            Student.status == "ACTIVE",
            Student.deleted_at.is_(None),
        )
        .first()
    )


def update_student_profile(
    db: Session,
    student_id: str,
    profile_data: ProfileUpdateRequest,
) -> Student | None:
    student = get_student_profile(
        db,
        student_id,
    )

    if not student:
        return None

    student.first_name = profile_data.first_name.strip()
    student.last_name = profile_data.last_name.strip()
    student.date_of_birth = profile_data.date_of_birth
    student.phone = profile_data.phone.strip() if profile_data.phone else None
    student.email = profile_data.email.strip()

    db.commit()
    db.refresh(student)

    return student