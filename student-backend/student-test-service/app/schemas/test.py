from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.question import QuestionResponse


class ProviderResponse(BaseModel):
    provider_id: UUID
    name: str


class BranchResponse(BaseModel):
    branch_id: UUID
    name: str
    city: str | None
    state: str | None


class SectionResponse(BaseModel):
    position: int
    subject: str
    status: str                       # PAPER, or NOT_ENROLLED / NO_SUBMISSION / NOT_APPROVED / NOT_COVERED / CONFLICT
    message: str | None = None        # why there is no paper (None for PAPER)
    question_count: int = 0


class TestResponse(BaseModel):
    test_id: UUID
    provider: ProviderResponse
    branch: BranchResponse
    name: str
    description: str | None
    duration_minutes: int
    total_questions: int
    total_marks: int
    start_at: datetime | None
    end_at: datetime | None
    status: str
    global_occurrence_id: UUID | None = None      # a unified Global Test (shown as the Global Test, not as the provider above)
    sections: list[SectionResponse] | None = None  # unified Global Test: every configured subject in order (None for other tests)


class TestStartResponse(TestResponse):
    questions: list[QuestionResponse]