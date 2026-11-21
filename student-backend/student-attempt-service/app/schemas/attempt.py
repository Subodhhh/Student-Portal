from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.marks import Marks


class StartAttemptRequest(BaseModel):
    test_id: UUID


class AttemptResponse(BaseModel):
    attempt_id: UUID
    test_id: UUID
    started_at: datetime
    expires_at: datetime
    status: str


class NoAttemptResponse(BaseModel):
    """GET /attempts/test/{test_id} when the student has not started the test: an explicit, successful answer ("no attempt yet"),
    not an error. An existing attempt is still returned as AttemptResponse, unchanged."""
    has_attempt: Literal[False] = False


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
    marks: Marks
    negative_marks: Marks
    image_reference: str | None
    question_image: str | None = None
    option_a_image: str | None = None
    option_b_image: str | None = None
    option_c_image: str | None = None
    option_d_image: str | None = None
    content: dict | None = None          # MSQ options / NAT number format (never the key)
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