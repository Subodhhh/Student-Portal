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
    global_occurrence_id: UUID | None = None   # a unified Global Test: not owned by provider_id (only its anchor), labelled "Global Test"
    is_retake: bool = False                    # the result of a provider-authorized retake
    superseded: bool = False                   # an original replaced by its evaluated retake (kept for audit; not official)
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
    status: str | None = None        # unified Global Test: PAPER, or why the student had no paper for this subject
    message: str | None = None

    total_questions: int
    attempted_questions: int
    correct_answers: int
    incorrect_answers: int
    unanswered_questions: int

    total_marks: int
    obtained_marks: int
    accuracy: float
    adjustment: int = 0              # a Global Test: this subject's manual adjustment (included in obtained_marks)


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
    # False while the test can still be started by others: questions is then empty and review opens at review_available_at.
    review_available: bool = True
    review_available_at: datetime | None = None
    # a Global Test: a manual adjustment never attributed to a subject (included in the overall result only)
    unattributed_adjustment: int | None = None