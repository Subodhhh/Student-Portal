from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: str
    first_name: str
    last_name: str
    date_of_birth: date | None
    phone: str | None
    email: EmailStr


class ProfileUpdateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    date_of_birth: date | None = None
    phone: str | None = Field(default=None, max_length=20)
    email: EmailStr