from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SaveAnswerRequest(BaseModel):
    question_id: UUID
    # validated and canonicalised server-side against the question's type (services/attempt_answer_service.canonical_answer)
    selected_answer: str | None = Field(default=None, max_length=200)


class AttemptAnswerResponse(BaseModel):
    answer_id: UUID
    attempt_id: UUID
    question_id: UUID
    selected_answer: str | None
    question_state: str
    visited_at: datetime | None
    answered_at: datetime | None
    updated_at: datetime