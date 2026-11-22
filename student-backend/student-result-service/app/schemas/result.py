from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ResultResponse(BaseModel):
    result_id: UUID
    attempt_id: UUID
    test_id: UUID
    student_id: str

    total_questions: int
    attempted_questions: int
    correct_answers: int
    incorrect_answers: int
    unanswered_questions: int

    total_marks: float
    obtained_marks: float
    accuracy: float

    created_at: datetime


class ResultListItem(BaseModel):
    result_id: UUID
    attempt_id: UUID
    test_id: UUID
    student_id: str

    test_name: str
    global_occurrence_id: UUID | None = None
    is_retake: bool = False
    superseded: bool = False
    provider_id: UUID
    provider_name: str

    branch_id: UUID
    branch_name: str
    city: str | None
    state: str | None

    duration_minutes: int

    total_questions: int
    attempted_questions: int
    correct_answers: int
    incorrect_answers: int
    unanswered_questions: int

    # CHANGED TO FLOAT HERE:
    total_marks: float
    obtained_marks: float
    accuracy: float

    created_at: datetime


class SubjectPerformance(BaseModel):
    subject: str
    status: str | None = None
    message: str | None = None

    total_questions: int
    attempted_questions: int
    correct_answers: int
    incorrect_answers: float
    unanswered_questions: int

    # CHANGED TO FLOAT HERE:
    total_marks: float
    obtained_marks: float
    accuracy: float
    adjustment: int = 0              


class QuestionReview(BaseModel):
    question_id: UUID
    question_order: int
    subject: str | None

    question_text: str

    option_a: str | None
    option_b: str | None
    option_c: str | None
    option_d: str | None

    selected_answer: str | None
    correct_answer: str

    status: str

    marks_obtained: float
    marks: float
    negative_marks: float

    image_reference: str | None
    question_image: str | None = None
    option_a_image: str | None = None
    option_b_image: str | None = None
    option_c_image: str | None = None
    option_d_image: str | None = None


class ResultDetailsResponse(BaseModel):
    result: ResultResponse
    subjects: list[SubjectPerformance]
    questions: list[QuestionReview]
    review_available: bool = True
    review_available_at: datetime | None = None
    unattributed_adjustment: int | None = None