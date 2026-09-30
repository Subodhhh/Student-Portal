from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.auth import (
    create_session,
    delete_session,
    delete_student_sessions,
    get_session,
    redis_client,
)
from app.config import settings
from app.database import SessionLocal
from app.models.password_reset_token import PasswordSetupResetToken
from app.models.student import Student
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    ResetPasswordRequest,
)
from app.services.email import send_password_reset_email
from app.services.password import hash_password, verify_password
from app.services.password_reset import generate_reset_token

router = APIRouter(prefix="/auth", tags=["Authentication"])

LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60

FORGOT_PASSWORD_LIMIT = 3
FORGOT_PASSWORD_WINDOW_SECONDS = 15 * 60


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    current_count = redis_client.incr(key)

    if current_count == 1:
        redis_client.expire(key, window_seconds)

    return current_count <= limit


def clear_rate_limit(key: str) -> None:
    redis_client.delete(key)


@router.post("/login", response_model=LoginResponse)
def login(
    login_data: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    client_ip = get_client_ip(request)
    rate_limit_key = f"auth_login_attempts:{client_ip}"

    if not check_rate_limit(
        rate_limit_key,
        LOGIN_LIMIT,
        LOGIN_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
        )

    login_value = login_data.login.strip()

    student = (
        db.query(Student)
        .filter(
            (Student.student_id == login_value)
            | (Student.email == login_value)
        )
        .first()
    )

    if not student or not student.password_hash:
        raise HTTPException(
            status_code=401,
            detail="Invalid login credentials",
        )

    if student.status != "ACTIVE":
        raise HTTPException(
            status_code=401,
            detail="Invalid login credentials",
        )

    if not verify_password(
        login_data.password,
        student.password_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid login credentials",
        )

    clear_rate_limit(rate_limit_key)

    session_id, _ = create_session(str(student.student_id))

    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=1800,
    )

    return {"message": "Login successful"}


@router.get("/me")
def get_current_student(
    session_id: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    session = get_session(session_id)

    if not session:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    expires_at = datetime.fromisoformat(session["expires_at"])

    if expires_at <= datetime.now(timezone.utc):
        delete_session(session_id)
        raise HTTPException(
            status_code=401,
            detail="Session expired",
        )

    student_id = session["student_id"]

    student = (
        db.query(Student)
        .filter(Student.student_id == student_id)
        .first()
    )

    if not student or student.status != "ACTIVE":
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return {
        "student_id": str(student.student_id),
        "first_name": student.first_name,
        "last_name": student.last_name,
        "email": student.email,
    }


@router.post("/logout")
def logout(
    response: Response,
    session_id: str | None = Cookie(default=None),
):
    if session_id:
        delete_session(session_id)

    response.delete_cookie(key="session_id")

    return {"message": "Logout successful"}


@router.post("/forgot-password")
def forgot_password(
    request: Request,
    password_request: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    client_ip = get_client_ip(request)
    rate_limit_key = f"auth_forgot_password:{client_ip}"

    if not check_rate_limit(
        rate_limit_key,
        FORGOT_PASSWORD_LIMIT,
        FORGOT_PASSWORD_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many password reset requests. Please try again later.",
        )

    student = (
        db.query(Student)
        .filter(Student.email == password_request.email.strip())
        .first()
    )

    generic_response = {
        "message": "If the email is registered, a reset link has been sent."
    }

    if not student or student.status != "ACTIVE":
        return generic_response

    now = datetime.now(timezone.utc)

    old_tokens = (
        db.query(PasswordSetupResetToken)
        .filter(
            PasswordSetupResetToken.student_id == student.student_id,
            PasswordSetupResetToken.token_type == "PASSWORD_RESET",
            PasswordSetupResetToken.used_at.is_(None),
            PasswordSetupResetToken.expires_at > now.replace(tzinfo=None),
        )
        .all()
    )

    for token in old_tokens:
        token.used_at = now.replace(tzinfo=None)

    raw_token, token_hash, expires_at = generate_reset_token()

    reset_token = PasswordSetupResetToken(
        student_id=student.student_id,
        token_hash=token_hash,
        token_type="PASSWORD_RESET",
        expires_at=expires_at.replace(tzinfo=None),
    )

    db.add(reset_token)
    db.commit()

    reset_url = f"{settings.frontend_reset_url}?token={raw_token}"

    if settings.resend_api_key and settings.email_from:
        send_password_reset_email(
            recipient_email=student.email,
            reset_url=reset_url,
        )

    return generic_response


@router.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    import hashlib

    token_hash = hashlib.sha256(
        request.token.encode()
    ).hexdigest()

    token = (
        db.query(PasswordSetupResetToken)
        .filter(
            PasswordSetupResetToken.token_hash == token_hash,
            PasswordSetupResetToken.token_type == "PASSWORD_RESET",
            PasswordSetupResetToken.used_at.is_(None),
        )
        .first()
    )

    if not token:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired reset token",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if token.expires_at <= now:
        token.used_at = now
        db.commit()

        raise HTTPException(
            status_code=400,
            detail="Invalid or expired reset token",
        )

    student = (
        db.query(Student)
        .filter(Student.student_id == token.student_id)
        .first()
    )

    if not student or student.status != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired reset token",
        )

    student.password_hash = hash_password(request.new_password)

    delete_student_sessions(str(student.student_id))

    token.used_at = now

    other_tokens = (
        db.query(PasswordSetupResetToken)
        .filter(
            PasswordSetupResetToken.student_id == student.student_id,
            PasswordSetupResetToken.token_type == "PASSWORD_RESET",
            PasswordSetupResetToken.used_at.is_(None),
            PasswordSetupResetToken.token_id != token.token_id,
        )
        .all()
    )

    for other_token in other_tokens:
        other_token.used_at = now

    db.commit()

    return {"message": "Password reset successful"}