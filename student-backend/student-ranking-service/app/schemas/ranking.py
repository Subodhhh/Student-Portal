from uuid import UUID

from pydantic import BaseModel


class RankingItem(BaseModel):
    rank: int
    student_id: str
    student_name: str
    obtained_marks: int
    # unified Global Test only: the maximum marks delivered to this student and the percentage the ranking is based on
    max_marks: int | None = None
    percentage: float | None = None


class SubjectRanking(BaseModel):
    subject: str
    total_students: int              # students who were delivered questions for this subject and have a result
    my_rank: int | None
    my_score: int | None
    my_max_marks: int | None = None
    my_percentage: float | None = None
    leaderboard: list[RankingItem]


class RankingResponse(BaseModel):
    test_id: UUID
    my_student_id: str
    my_rank: int | None
    total_students: int
    my_score: int | None
    leaderboard: list[RankingItem]
    # unified Global Test: the overall ranking above is the whole occurrence's, ranked by percentage, plus one per configured subject
    occurrence_id: UUID | None = None
    my_max_marks: int | None = None
    my_percentage: float | None = None
    subjects: list[SubjectRanking] | None = None
