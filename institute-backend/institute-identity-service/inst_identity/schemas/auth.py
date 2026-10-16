from uuid import UUID

from pydantic import BaseModel, Field

from institute_common.core.roles import Role


class LoginRequest(BaseModel):
    staff_code: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=1, max_length=256)


class MfaVerifyRequest(BaseModel):
    challenge_id: str = Field(min_length=10, max_length=100)
    otp: str = Field(pattern=r"^\d{6}$")


class MfaResendRequest(BaseModel):
    challenge_id: str = Field(min_length=10, max_length=100)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str | None = Field(default=None, max_length=256)


class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=255)  # staff code or email


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class AssignmentOut(BaseModel):
    assignment_id: UUID
    role: Role
    provider_id: UUID | None
    branch_id: UUID | None


class MeOut(BaseModel):
    staff_id: UUID
    staff_code: str
    full_name: str
    email: str
    primary_role: Role
    assignments: list[AssignmentOut]
    permissions: list[str]
    csrf_token: str | None = None


class AuthResult(BaseModel):
    status: str  # AUTHENTICATED | MFA_REQUIRED
    challenge_id: str | None = None
    me: MeOut | None = None


class Message(BaseModel):
    message: str
