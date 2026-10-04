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
    answered_at: datetime
    updated_at: datetime