from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.test_result import TestResult
from app.repositories.result_repository import (
    create_result,
    get_attempt,
    get_existing_result,
    get_questions_with_answers,
    get_review_window,
    get_student_results,
)
from app.services.question_order import section_order
from app.services.reevaluation import score


def evaluate_attempt(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    existing_result = get_existing_result(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if existing_result:
        return existing_result

    attempt = get_attempt(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Attempt not found",
        )

    if attempt["status"] not in ("SUBMITTED", "AUTO_SUBMITTED"):
        raise HTTPException(
            status_code=400,
            detail="Test attempt is not submitted",
        )

    questions = get_questions_with_answers(
        db=db,
        attempt_id=attempt_id,
    )

    if not questions:
        raise HTTPException(
            status_code=400,
            detail="No questions found for this test",
        )

    # One scoring implementation for first evaluation and authorized re-evaluation (app/services/reevaluation.score):
    # +marks for a correct answer, -negative marks for a wrong one, 0 for unanswered.
    result = TestResult(
        attempt_id=attempt_id,
        test_id=attempt["test_id"],
        student_id=student_id,
        **score(questions),
    )

    try:
        return create_result(
            db=db,
            result=result,
        )
    except IntegrityError:
        db.rollback()

        existing_result = get_existing_result(
            db=db,
            attempt_id=attempt_id,
            student_id=student_id,
        )

        if existing_result:
            return existing_result

        raise HTTPException(
            status_code=500,
            detail="Unable to create test result",
        )


def get_result(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    result = get_existing_result(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Result not found",
        )

    return result


def get_results_for_student(
    db: Session,
    student_id: str,
):
    return get_student_results(
        db=db,
        student_id=student_id,
    )

def _sections(db: Session, test_id):
    from sqlalchemy import text
    value = db.execute(text("SELECT sections FROM tests WHERE test_id = :t"), {"t": test_id}).scalar()
    if isinstance(value, str):
        import json
        value = json.loads(value)
    return value


def get_result_details(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    result = get_result(
        db=db,
        attempt_id=attempt_id,
        student_id=student_id,
    )

    # The correct answers of a test must not leak while other students can still start it: question-level review
    # opens when the availability window has closed. Score and per-subject totals are shown right after evaluation.
    review = get_review_window(db=db, test_id=result["test_id"])
    review_available = bool(review is None or review["open"])

    questions = get_questions_with_answers(
        db=db,
        attempt_id=attempt_id,
    )

    if not questions:
        raise HTTPException(
            status_code=404,
            detail="No questions found for this test",
        )

    subjects = {}
    question_reviews = []

    for question in questions:
        subject = question["subject"] or "Other"

        if subject not in subjects:
            subjects[subject] = {
                "subject": subject,
                "total_questions": 0,
                "attempted_questions": 0,
                "correct_answers": 0,
                "incorrect_answers": 0,
                "unanswered_questions": 0,
                "total_marks": 0,
                "obtained_marks": 0,
            }

        subject_data = subjects[subject]

        marks = question["marks"] or 0
        negative_marks = question["negative_marks"] or 0

        selected_answer = question["selected_answer"]
        correct_answer = question["correct_answer"]

        subject_data["total_questions"] += 1
        subject_data["total_marks"] += marks

        if not selected_answer:
            status = "UNANSWERED"
            marks_obtained = 0
            subject_data["unanswered_questions"] += 1

        elif selected_answer.strip().upper() == correct_answer.strip().upper():
            status = "CORRECT"
            marks_obtained = marks

            subject_data["attempted_questions"] += 1
            subject_data["correct_answers"] += 1
            subject_data["obtained_marks"] += marks

        else:
            status = "INCORRECT"
            marks_obtained = -negative_marks

            subject_data["attempted_questions"] += 1
            subject_data["incorrect_answers"] += 1
            subject_data["obtained_marks"] -= negative_marks

        question_reviews.append(
            {
                "question_id": question["question_id"],
                "question_order": question["question_order"],
                "subject": question["subject"],
                "question_text": question["question_text"],
                "option_a": question["option_a"],
                "option_b": question["option_b"],
                "option_c": question["option_c"],
                "option_d": question["option_d"],
                "selected_answer": selected_answer,
                "correct_answer": correct_answer,
                "status": status,
                "marks_obtained": marks_obtained,
                "marks": marks,
                "negative_marks": negative_marks,
                "image_reference": question["image_reference"],
            }
        )

    subject_performance = []

    for subject_data in subjects.values():
        attempted = subject_data["attempted_questions"]
        correct = subject_data["correct_answers"]

        accuracy = (
            (correct / attempted) * 100
            if attempted
            else 0.0
        )

        subject_data["accuracy"] = round(
            accuracy,
            2,
        )

        subject_performance.append(subject_data)

    sections = _sections(db, result["test_id"])
    if sections:
        # a unified Global Test: every configured subject in the configured order; one without a paper is shown as unavailable with
        # no questions and no marks (nothing is invented); the subjects with questions are exactly the ones scored above
        by_name = {s["subject"]: s for s in subject_performance}
        subject_performance = [{**(by_name.get(x["subject"]) or {"subject": x["subject"], "total_questions": 0, "attempted_questions": 0,
                                                                 "correct_answers": 0, "incorrect_answers": 0, "unanswered_questions": 0,
                                                                 "total_marks": 0, "obtained_marks": 0, "accuracy": 0.0}),
                                "status": x["status"], "message": x.get("message")} for x in sections]
        question_reviews = section_order(question_reviews, sections, student_id, result["test_id"],
                                         subject_of=lambda q: q["subject"], id_of=lambda q: q["question_id"])

    return {
        "result": result,
        "subjects": subject_performance,
        "questions": question_reviews if review_available else [],
        "review_available": review_available,
        "review_available_at": None if review_available or review is None else review["end_at"],
    }