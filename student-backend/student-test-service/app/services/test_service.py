from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.question_repository import (
    get_questions_by_test,
)

from app.repositories.test_repository import (
    get_active_tests,
    get_scheduled_tests,
    get_missed_tests,
    get_test_by_id,
    get_test_for_student,
    has_running_attempt,
)


def session_zone(db: Session):
    """The zone tests.start_at/end_at are written in: the DB session's TimeZone (routes/internal._to_session_naive); UTC elsewhere."""
    if "session_zone" not in db.info:
        name = db.execute(text("SELECT current_setting('TimeZone')")).scalar_one() if db.get_bind().dialect.name == "postgresql" else "UTC"
        try:
            db.info["session_zone"] = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            db.info["session_zone"] = None          # an offset-style setting: converted by PostgreSQL itself below
    return db.info["session_zone"]


def as_instant(db: Session, value):
    """A naive start/end (wall clock in the session zone) as an aware UTC instant, so no client reinterprets it in its own
    browser zone (a naive '10:00' was read as local time: 5h30 off in India). Aware values pass through; storage is untouched."""
    if value is None or value.tzinfo is not None:
        return value
    zone = session_zone(db)
    if zone is None:
        return db.execute(text("SELECT CAST(:v AS timestamp) AT TIME ZONE current_setting('TimeZone')"), {"v": value}).scalar_one()
    return value.replace(tzinfo=zone).astimezone(timezone.utc)


def format_test(row, db: Session):
    if not row:
        return None

    return {
        "test_id": row["test_id"],
        "provider": {
            "provider_id": row["provider_id"],
            "name": row["provider_name"],
        },
        "branch": {
            "branch_id": row["branch_id"],
            "name": row["branch_name"],
            "city": row["city"],
            "state": row["state"],
        },
        "name": row["name"],
        "description": row["description"],
        "duration_minutes": row["duration_minutes"],
        "total_questions": row["total_questions"],
        "total_marks": row["total_marks"],
        "start_at": as_instant(db, row["start_at"]),
        "end_at": as_instant(db, row["end_at"]),
        "status": row["status"],
        "global_occurrence_id": row.get("global_occurrence_id"),   # set: a unified Global Test (show it as the Global Test, not a provider's)
    }


def get_test(
    db: Session,
    test_id,
    student_id: str,
):
    row = get_test_by_id(
        db=db,
        test_id=test_id,
        student_id=student_id,
    )

    return format_test(row, db)


def get_test_details(
    db: Session,
    test_id,
    student_id: str,
):
    row = get_test_for_student(
        db=db,
        test_id=test_id,
        student_id=student_id,
    )

    return format_test(row, db)


def get_available_tests(
    db: Session,
    student_id: str,
):
    rows = get_active_tests(
        db=db,
        student_id=student_id,
    )

    return [
        format_test(row, db)
        for row in rows
    ]


def get_scheduled_tests_for_student(
    db: Session,
    student_id: str,
):
    rows = get_scheduled_tests(
        db=db,
        student_id=student_id,
    )

    return [
        format_test(row, db)
        for row in rows
    ]


def get_missed_tests_for_student(
    db: Session,
    student_id: str,
):
    rows = get_missed_tests(
        db=db,
        student_id=student_id,
    )

    return [
        format_test(row, db)
        for row in rows
    ]


def get_test_for_start(
    db: Session,
    test_id,
    student_id: str,
):
    row = get_test_by_id(
        db=db,
        test_id=test_id,
        student_id=student_id,
    )

    if not row and has_running_attempt(db=db, test_id=test_id, student_id=student_id):
        # An attempt started inside the window keeps its full duration even after the window closes, so the student must
        # still be able to load the paper to resume it (eligibility is still checked by get_test_for_student).
        row = get_test_for_student(
            db=db,
            test_id=test_id,
            student_id=student_id,
        )

    if not row:
        return None

    test = format_test(row, db)

    questions = get_questions_by_test(
        db=db,
        test_id=test_id,
    )

    test["questions"] = [
        {
            "question_id": question.question_id,
            "question_order": question.question_order,
            "subject": question.subject,
            "question_text": question.question_text,
            "question_type": question.question_type,
            "option_a": question.option_a,
            "option_b": question.option_b,
            "option_c": question.option_c,
            "option_d": question.option_d,
            "marks": question.marks,
            "negative_marks": question.negative_marks,
            "image_reference": question.image_reference,
        }
        for question in questions
    ]

    return test