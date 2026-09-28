import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.password_reset_token import PasswordSetupResetToken
from app.models.student import Student
from app.services.password import hash_password, verify_password


def test_password_reset_flow():
    db: Session = SessionLocal()

    try:
        student = (
            db.query(Student)
            .filter(Student.email == "test.student@example.com")
            .first()
        )

        assert student is not None

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(
            raw_token.encode()
        ).hexdigest()

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        token = PasswordSetupResetToken(
            student_id=student.student_id,
            token_hash=token_hash,
            token_type="PASSWORD_RESET",
            expires_at=now + timedelta(minutes=30),
        )

        db.add(token)
        db.commit()
        db.refresh(token)

        new_password = secrets.token_urlsafe(16)

        student.password_hash = hash_password(new_password)
        token.used_at = now

        db.commit()

        db.refresh(student)
        db.refresh(token)

        assert verify_password(
            new_password,
            student.password_hash,
        )

        assert token.used_at is not None

        stored_hash = hashlib.sha256(
            raw_token.encode()
        ).hexdigest()

        assert stored_hash == token.token_hash

    finally:
        db.rollback()

        test_tokens = (
            db.query(PasswordSetupResetToken)
            .filter(
                PasswordSetupResetToken.student_id == student.student_id,
                PasswordSetupResetToken.token_hash == token_hash,
            )
            .all()
        )

        for test_token in test_tokens:
            db.delete(test_token)

        db.commit()
        db.close()