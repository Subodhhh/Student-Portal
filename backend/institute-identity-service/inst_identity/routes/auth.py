from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from institute_common.config import settings
from institute_common.core import audit, ratelimit, sessions
from institute_common.core.email import Mail, get_sender
from institute_common.core.passwords import hash_password, validate_password, verify_password
from institute_common.core.roles import MFA_ROLES, RANK, ROLE_PERMISSIONS, Role
from institute_common.core.security import (
    Principal, api_error, client_ip, cookie_name, get_principal, load_assignments, origin_check,
)
from institute_common.database import get_db, utcnow
from institute_common.models.staff import Staff
from inst_identity.schemas.auth import (
    AssignmentOut, AuthResult, ChangePasswordRequest, ForgotPasswordRequest, LoginRequest,
    MeOut, Message, MfaResendRequest, MfaVerifyRequest, ResetPasswordRequest, VerifyEmailRequest,
)
from inst_identity.services import tokens

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"], dependencies=[Depends(origin_check)])

WINDOW = 15 * 60
GENERIC_LOGIN_FAIL = "Invalid Staff ID or password."


def _me(staff: Staff, assignments, csrf: str | None = None) -> MeOut:
    roles = {Role(a.role) for a in assignments}
    perms: set[str] = set()
    for r in roles:
        perms |= ROLE_PERMISSIONS[r]
    primary = max(roles, key=lambda r: RANK[r])
    return MeOut(
        staff_id=staff.staff_id, staff_code=staff.staff_code, full_name=staff.full_name,
        email=staff.email, primary_role=primary,
        assignments=[AssignmentOut(assignment_id=a.assignment_id, role=a.role,
                                   provider_id=a.provider_id, branch_id=a.branch_id) for a in assignments],
        permissions=sorted(perms), csrf_token=csrf,
    )


def _start_session(response: Response, db: Session, staff: Staff, ip: str) -> AuthResult:
    assignments = load_assignments(db, staff.staff_id)
    sid, csrf = sessions.create_session(str(staff.staff_id))
    response.set_cookie(
        key=cookie_name(), value=sid, httponly=True, secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite.lower(), max_age=int(sessions.ABSOLUTE_LIFETIME.total_seconds()),
        path="/",  # no Domain attribute => host-only
    )
    audit.record(db, "auth.login", "SUCCESS", actor=staff.staff_id, ip=ip)
    return AuthResult(status="AUTHENTICATED", me=_me(staff, assignments, csrf))


def _send_otp(staff: Staff, otp: str) -> None:
    get_sender().send(
        Mail(to=staff.email, subject="Your Institute Portal verification code",
             body=f"Your verification code is {otp}. It expires in 5 minutes. "
                  "If you did not try to sign in, change your password.")
    )


def _password_problems(password: str, staff: Staff) -> None:
    problems = validate_password(password, personal=[staff.staff_code, staff.full_name, staff.email.split("@")[0]])
    if problems:
        raise api_error(422, "WEAK_PASSWORD", " ".join(problems))


@router.post("/login", response_model=AuthResult)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    code = body.staff_code.strip().upper()
    if not (ratelimit.hit("login", "ip", ip, 30, WINDOW) and ratelimit.hit("login", "acct", code, 5, WINDOW)):
        audit.record(db, "auth.login", "DENIED", ip=ip, details={"reason": "rate_limited", "staff_code": code})
        raise api_error(429, "RATE_LIMITED", "Too many attempts. Please try again later.")

    staff = db.scalar(select(Staff).where(Staff.staff_code == code))
    ok = verify_password(body.password, staff.password_hash if staff else None)
    assignments = load_assignments(db, staff.staff_id) if staff else []
    if not (staff and ok and staff.status == "ACTIVE" and assignments):
        audit.record(db, "auth.login", "FAILURE", actor=staff.staff_id if staff else None, ip=ip,
                     details={"staff_code": code})
        raise api_error(401, "INVALID_CREDENTIALS", GENERIC_LOGIN_FAIL)

    if any(Role(a.role) in MFA_ROLES for a in assignments):
        if staff.email_verified_at is None:
            raise api_error(403, "EMAIL_NOT_VERIFIED", "Verify your email before signing in.")
        if not ratelimit.hit("mfa_issue", "acct", str(staff.staff_id), 5, WINDOW):
            raise api_error(429, "RATE_LIMITED", "Too many verification codes requested.")
        cid, otp = sessions.create_challenge(str(staff.staff_id))
        _send_otp(staff, otp)
        audit.record(db, "auth.mfa_issued", "SUCCESS", actor=staff.staff_id, ip=ip)
        return AuthResult(status="MFA_REQUIRED", challenge_id=cid)

    ratelimit.reset("login", "acct", code)
    return _start_session(response, db, staff, ip)


@router.post("/mfa/verify", response_model=AuthResult)
def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if not (ratelimit.hit("mfa_verify", "ip", ip, 30, WINDOW)
            and ratelimit.hit("mfa_verify", "challenge", body.challenge_id, 10, WINDOW)):
        raise api_error(429, "RATE_LIMITED", "Too many attempts. Please try again later.")
    staff_id = sessions.verify_otp(body.challenge_id, body.otp)
    if not staff_id:
        audit.record(db, "auth.mfa_verify", "FAILURE", ip=ip)
        raise api_error(401, "INVALID_OTP", "Invalid or expired verification code.")
    staff = db.get(Staff, UUID(staff_id))
    if not staff or staff.status != "ACTIVE" or not load_assignments(db, staff.staff_id):
        raise api_error(401, "INVALID_OTP", "Invalid or expired verification code.")
    ratelimit.reset("login", "acct", staff.staff_code)
    return _start_session(response, db, staff, ip)


@router.post("/mfa/resend", response_model=Message)
def mfa_resend(body: MfaResendRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if not ratelimit.hit("mfa_resend", "ip", ip, 10, WINDOW):
        raise api_error(429, "RATE_LIMITED", "Too many requests.")
    result = sessions.reissue_otp(body.challenge_id)
    if result is None:
        raise api_error(401, "INVALID_CHALLENGE", "Sign in again.")
    staff_id, otp = result
    if staff_id == "COOLDOWN":
        raise api_error(429, "RESEND_COOLDOWN", "Please wait 60 seconds before requesting another code.")
    staff = db.get(Staff, UUID(staff_id))
    if not staff or staff.status != "ACTIVE":
        raise api_error(401, "INVALID_CHALLENGE", "Sign in again.")
    if not ratelimit.hit("mfa_issue", "acct", staff_id, 5, WINDOW):
        raise api_error(429, "RATE_LIMITED", "Too many verification codes requested.")
    _send_otp(staff, otp)
    return Message(message="A new verification code was sent.")


@router.post("/logout", response_model=Message)
def logout(response: Response, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    sessions.delete_session(principal.session_id)
    response.delete_cookie(cookie_name(), path="/")
    audit.record(db, "auth.logout", "SUCCESS", actor=principal.staff_id, ip=principal.ip)
    return Message(message="Logged out.")


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(get_principal)):
    if not principal.assignments:
        raise api_error(403, "NO_ACTIVE_ROLE", "No active role assignment.")
    return _me(principal.staff, principal.assignments, principal.csrf)


@router.post("/verify-email", response_model=Message)
def verify_email(body: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if not ratelimit.hit("verify_email", "ip", ip, 20, WINDOW):
        raise api_error(429, "RATE_LIMITED", "Too many attempts.")
    # Validate the password first so weak input does not burn the single-use token.
    staff = tokens.peek_token(db, body.token, "EMAIL_VERIFY")
    if staff and staff.password_hash is None:
        if not body.new_password:
            raise api_error(422, "PASSWORD_REQUIRED", "Choose a password to finish activating your account.")
        _password_problems(body.new_password, staff)
    staff = tokens.consume_token(db, body.token, "EMAIL_VERIFY")
    if not staff:
        raise api_error(400, "INVALID_TOKEN", "This link is invalid or has expired.")
    staff.email_verified_at = utcnow()
    if staff.password_hash is None:
        staff.password_hash = hash_password(body.new_password)
        staff.password_changed_at = utcnow()
    db.commit()
    audit.record(db, "auth.email_verified", "SUCCESS", actor=staff.staff_id, ip=ip)
    return Message(message="Email verified.")


@router.post("/forgot-password", response_model=Message)
def forgot_password(body: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    ident = body.identifier.strip()
    generic = Message(message="If the account exists and its email is verified, a reset link has been sent.")
    if not (ratelimit.hit("forgot", "ip", ip, 10, WINDOW) and ratelimit.hit("forgot", "acct", ident.lower(), 3, WINDOW)):
        return generic  # do not reveal throttling per account either
    staff = db.scalar(select(Staff).where((Staff.staff_code == ident.upper()) | (Staff.email == ident.lower())))
    if staff and staff.status == "ACTIVE" and staff.email_verified_at is not None:
        tokens.issue_token(db, staff, "PASSWORD_RESET")
        audit.record(db, "auth.password_reset_requested", "SUCCESS", actor=staff.staff_id, ip=ip)
    return generic


@router.post("/reset-password", response_model=Message)
def reset_password(body: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if not ratelimit.hit("reset", "ip", ip, 20, WINDOW):
        raise api_error(429, "RATE_LIMITED", "Too many attempts.")
    pending = tokens.peek_token(db, body.token, "PASSWORD_RESET")
    if pending:
        _password_problems(body.new_password, pending)
    staff = tokens.consume_token(db, body.token, "PASSWORD_RESET")
    if not staff:
        raise api_error(400, "INVALID_TOKEN", "This link is invalid or has expired.")
    staff.password_hash = hash_password(body.new_password)
    staff.password_changed_at = utcnow()
    db.commit()
    sessions.invalidate_staff_sessions(str(staff.staff_id))
    audit.record(db, "auth.password_reset", "SUCCESS", actor=staff.staff_id, ip=ip)
    return Message(message="Password updated. Please sign in.")


@router.post("/change-password", response_model=Message)
def change_password(body: ChangePasswordRequest, response: Response,
                    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    staff = principal.staff
    if not ratelimit.hit("change_pw", "acct", str(staff.staff_id), 5, WINDOW):
        raise api_error(429, "RATE_LIMITED", "Too many attempts.")
    if not verify_password(body.current_password, staff.password_hash):
        audit.record(db, "auth.password_change", "FAILURE", actor=staff.staff_id, ip=principal.ip)
        raise api_error(403, "REAUTH_FAILED", "Current password is incorrect.")
    _password_problems(body.new_password, staff)
    staff.password_hash = hash_password(body.new_password)
    staff.password_changed_at = utcnow()
    db.commit()
    sessions.invalidate_staff_sessions(str(staff.staff_id))  # includes the current session
    response.delete_cookie(cookie_name(), path="/")
    audit.record(db, "auth.password_change", "SUCCESS", actor=staff.staff_id, ip=principal.ip)
    return Message(message="Password changed. Please sign in again.")
