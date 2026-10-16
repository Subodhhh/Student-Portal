from datetime import date

from pydantic import BaseModel


class BatchResponse(BaseModel):
    batch_id: str
    name: str
    start_date: date | None
    end_date: date | None


class ProviderResponse(BaseModel):
    provider_id: str
    name: str
    email: str | None
    phone: str | None
    address: str | None


class BranchResponse(BaseModel):
    branch_id: str
    name: str
    city: str | None
    state: str | None
    address: str | None
    pincode: str | None


class EnrollmentResponse(BaseModel):
    enrollment_id: str
    provider: ProviderResponse
    branch: BranchResponse
    status: str
    enrolled_at: date
    batches: list[BatchResponse]