from uuid import UUID

from pydantic import BaseModel


class QuestionResponse(BaseModel):
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