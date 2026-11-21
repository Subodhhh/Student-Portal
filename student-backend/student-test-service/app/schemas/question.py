from uuid import UUID

from pydantic import BaseModel

from app.marks import Marks


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
    marks: Marks
    negative_marks: Marks
    image_reference: str | None
    question_image: str | None = None
    option_a_image: str | None = None
    option_b_image: str | None = None
    option_c_image: str | None = None
    option_d_image: str | None = None
    # MSQ options (with picture checksums) / NAT number format; never the key (answer_key and scheme are not part of this response)
    content: dict | None = None
