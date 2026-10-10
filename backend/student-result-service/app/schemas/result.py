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

    total_marks: int
    obtained_marks: int
    accuracy: float

    created_at: datetime


class ResultListItem(BaseModel):
    result_id: UUID
    attempt_id: UUID
    test_id: UUID
    student_id: str

    test_name: str
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

    total_marks: int
    obtained_marks: int
    accuracy: float

    created_at: datetime

class SubjectPerformance(BaseModel):
    subject: str

    total_questions: int
    attempted_questions: int
    correct_answers: int
    incorrect_answers: int
    unanswered_questions: int

    total_marks: int
    obtained_marks: int
    accuracy: float


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

    marks_obtained: int
    marks: int
    negative_marks: int

    image_reference: str | None


class ResultDetailsResponse(BaseModel):
    result: ResultResponse
    subjects: list[SubjectPerformance]
    questions: list[QuestionReview]