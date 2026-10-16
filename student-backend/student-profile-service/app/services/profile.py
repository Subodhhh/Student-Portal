from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.student import Student
from app.schemas.profile import ProfileUpdateRequest


def _password_matches(password: str, password_hash: str | None) -> bool:
    if not password or not password_hash:
        return False
    from argon2 import PasswordHasher
    from argon2.exceptions import Argon2Error, InvalidHashError

    try:
        return PasswordHasher().verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


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

    # Emails are stored lower-case everywhere (login, password reset and the Institute's identity matching compare that way).
    new_email = str(profile_data.email).strip().lower()

    if new_email != (student.email or "").lower():
        if not _password_matches(profile_data.current_password or "", student.password_hash):
            raise HTTPException(
                status_code=403,
                detail="Enter your current password to change your email address.",
            )

    student.first_name = profile_data.first_name.strip()
    student.last_name = profile_data.last_name.strip()
    student.date_of_birth = profile_data.date_of_birth
    student.phone = profile_data.phone.strip() if profile_data.phone else None
    student.email = new_email

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This email address is already used by another account.",
        )

    db.refresh(student)

    return student
