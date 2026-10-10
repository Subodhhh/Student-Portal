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
)


def format_test(row):
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
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "status": row["status"],
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

    return format_test(row)


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

    return format_test(row)


def get_available_tests(
    db: Session,
    student_id: str,
):
    rows = get_active_tests(
        db=db,
        student_id=student_id,
    )

    return [
        format_test(row)
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
        format_test(row)
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
        format_test(row)
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

    if not row:
        return None

    test = format_test(row)

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