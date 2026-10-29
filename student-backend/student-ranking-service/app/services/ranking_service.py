import json
from fractions import Fraction
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.ranking_repository import (
    get_global_occurrence,
    get_occurrence_results,
    get_occurrence_subject_scores,
    get_test_rankings,
)


def _not_available():
    return HTTPException(
        status_code=404,
        detail="Ranking not available",
    )


def get_ranking(
    db: Session,
    test_id: UUID,
    student_id: str,
):
    test = get_global_occurrence(db=db, test_id=test_id)
    if test and test["global_occurrence_id"]:
        return get_occurrence_ranking(db, test_id, test["global_occurrence_id"], test["sections"], student_id)

    rows = get_test_rankings(
        db=db,
        test_id=test_id,
    )

    # A leaderboard exposes other students' names and marks: only students who have their own result in this test may see it
    # (a test id alone must not reveal another provider's or branch's results).
    if not any(row["student_id"] == student_id for row in rows):
        raise _not_available()

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


def rank(entries: list[dict], student_id: str, *, raw_marks_order: bool) -> dict:
    """Standard competition ranking by percentage of the delivered maximum (1, 1, 3): equal percentages share a rank, compared exactly
    (fractions, never rounded floats). raw_marks_order only orders entries inside a shared rank (overall ranking); it never splits it."""
    def pct(e):
        return Fraction(e["obtained_marks"], e["max_marks"])

    entries = sorted(entries, key=lambda e: (-pct(e), -e["obtained_marks"] if raw_marks_order else 0, e["student_name"], e["student_id"]))
    leaderboard, previous, current = [], None, 0
    for index, e in enumerate(entries, start=1):
        if pct(e) != previous:
            current, previous = index, pct(e)
        leaderboard.append({**e, "rank": current, "percentage": round(float(pct(e)) * 100, 2)})
    mine = next((e for e in leaderboard if e["student_id"] == student_id), None)
    return {
        "total_students": len(leaderboard),
        "my_rank": mine["rank"] if mine else None,
        "my_score": mine["obtained_marks"] if mine else None,
        "my_max_marks": mine["max_marks"] if mine else None,
        "my_percentage": mine["percentage"] if mine else None,
        "leaderboard": leaderboard,
    }


def get_occurrence_ranking(db: Session, test_id: UUID, occurrence_id: UUID, sections, student_id: str):
    """A unified Global Test: one overall ranking of every student with an evaluated result in the occurrence (whichever Student test
    they were given), plus one ranking per configured subject with only the students who received questions for it. Occurrences are
    never mixed. Only students with their own result in the occurrence may see it; nothing about papers or answers is returned."""
    results = get_occurrence_results(db=db, occurrence_id=occurrence_id)
    if not any(r["student_id"] == student_id for r in results):
        raise _not_available()
    names = {r["student_id"]: r["student_name"] for r in results}
    overall = rank([{"student_id": r["student_id"], "student_name": r["student_name"], "obtained_marks": r["obtained_marks"],
                     "max_marks": r["total_marks"]} for r in results if r["total_marks"]], student_id, raw_marks_order=True)

    if isinstance(sections, str):
        sections = json.loads(sections)
    scores = get_occurrence_subject_scores(db=db, occurrence_id=occurrence_id)
    subjects = [s["subject"] for s in sorted(sections or [], key=lambda s: s["position"])] or sorted({s["subject"] for s in scores if s["subject"]})
    subject_rankings = []
    for subject in subjects:
        # only students who were delivered questions for the subject: a missing subject is never 0%
        entries = [{"student_id": s["student_id"], "student_name": names[s["student_id"]], "obtained_marks": int(s["obtained_marks"]),
                    "max_marks": int(s["max_marks"])} for s in scores if s["subject"] == subject and s["max_marks"]]
        subject_rankings.append({"subject": subject, **rank(entries, student_id, raw_marks_order=False)})

    return {
        "test_id": test_id,
        "occurrence_id": occurrence_id,
        "my_student_id": student_id,
        **overall,
        "subjects": subject_rankings,
    }
