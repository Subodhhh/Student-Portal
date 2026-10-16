from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    # Student logs in using an email and password.
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    message: str

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)