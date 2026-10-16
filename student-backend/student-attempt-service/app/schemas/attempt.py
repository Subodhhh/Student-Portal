from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class StartAttemptRequest(BaseModel):
    test_id: UUID


class AttemptResponse(BaseModel):
    attempt_id: UUID
    test_id: UUID
    started_at: datetime
    expires_at: datetime
    status: str


class WorkspaceQuestion(BaseModel):
    question_id: UUID
    question_order: int
    subject: str | None
    question_text: str
    question_type: str
    option_a: str | None
    option_b: str | None
    option_c: str | None
    option_d: str | None
    marks: int
    negative_marks: int
    image_reference: str | None
    selected_answer: str | None
    question_state: str


class AttemptWorkspaceResponse(BaseModel):
    attempt_id: UUID
    test_id: UUID
    started_at: datetime
    expires_at: datetime
    status: str
    remaining_seconds: int
    current_question_id: UUID | None
    questions: list[WorkspaceQuestion]


class AttemptSummaryResponse(BaseModel):
    attempt_id: UUID
    test_id: UUID
    total_questions: int
    answered: int
    not_answered: int
    marked_for_review: int
    not_visited: int