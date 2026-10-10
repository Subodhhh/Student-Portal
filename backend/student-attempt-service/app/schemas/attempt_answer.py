from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SaveAnswerRequest(BaseModel):
    question_id: UUID
    selected_answer: str | None


class AttemptAnswerResponse(BaseModel):
    answer_id: UUID
    attempt_id: UUID
    question_id: UUID
    selected_answer: str | None
    question_state: str
    visited_at: datetime | None
    answered_at: datetime | None
    updated_at: datetime