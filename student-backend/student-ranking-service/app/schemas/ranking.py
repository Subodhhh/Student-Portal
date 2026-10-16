from uuid import UUID

from pydantic import BaseModel


class RankingItem(BaseModel):
    rank: int
    student_id: str
    student_name: str
    obtained_marks: int


class RankingResponse(BaseModel):
    test_id: UUID
    my_student_id: str
    my_rank: int | None
    total_students: int
    my_score: int | None
    leaderboard: list[RankingItem]