from datetime import date

from pydantic import BaseModel


class BatchResponse(BaseModel):
    batch_id: str
    name: str


class ProviderResponse(BaseModel):
    provider_id: str
    name: str


class EnrollmentResponse(BaseModel):
    enrollment_id: str
    provider: ProviderResponse
    status: str
    enrolled_at: date
    batches: list[BatchResponse]