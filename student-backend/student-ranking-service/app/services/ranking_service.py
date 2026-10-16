from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.ranking_repository import (
    get_test_rankings,
)


def get_ranking(
    db: Session,
    test_id: UUID,
    student_id: str,
):
    rows = get_test_rankings(
        db=db,
        test_id=test_id,
    )

    # A leaderboard exposes other students' names and marks: only students who have their own result in this test may see it
    # (a test id alone must not reveal another provider's or branch's results).
    if not any(row["student_id"] == student_id for row in rows):
        raise HTTPException(
            status_code=404,
            detail="Ranking not available",
        )

    leaderboard = []

    my_rank = None
    my_score = None

    for index, row in enumerate(rows, start=1):
        item = {
            "rank": index,
            "student_id": row["student_id"],
            "student_name": row["student_name"],
            "obtained_marks": row["obtained_marks"],
        }

        leaderboard.append(item)

        if row["student_id"] == student_id:
            my_rank = index
            my_score = row["obtained_marks"]

    return {
        "test_id": test_id,
        "my_student_id": student_id,
        "my_rank": my_rank,
        "total_students": len(rows),
        "my_score": my_score,
        "leaderboard": leaderboard,
    }