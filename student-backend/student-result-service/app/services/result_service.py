import uuid
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import question_types
from app.marks import to_decimal
from app.models.test_result import TestResult
from app.repositories.result_repository import (
    create_result,
    get_attempt,
    get_existing_result,
    get_questions_with_answers,
    get_review_window,
    get_student_results,
)
from app.services import global_results, scoring
from app.services.question_order import section_order
from app.services.reevaluation import replace_question_rows, score


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
        result_id=uuid.uuid4(),
        attempt_id=attempt_id,
        test_id=attempt["test_id"],
        student_id=student_id,
        **score(questions),
    )
    # the marks awarded per question (D9), committed together with the result
    replace_question_rows(db, result.result_id, questions)

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


def _shuffled(db: Session, test_id) -> bool:
    from sqlalchemy import text
    return bool(db.execute(text("SELECT shuffle_questions FROM tests WHERE test_id = :t"), {"t": test_id}).scalar())


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
                "total_marks": Decimal(0),
                "obtained_marks": Decimal(0),
            }

        subject_data = subjects[subject]

        marks = to_decimal(question["marks"] or 0)
        negative_marks = to_decimal(question["negative_marks"] or 0)
        selected_answer = question["selected_answer"]
        qtype = scoring.question_type(question)
        key = scoring.key_of(question)
        correct_answer = key if qtype == "MCQ_SINGLE" else question_types.describe_key(qtype, key)
        subject_data["total_questions"] += 1
        subject_data["total_marks"] += marks
        status, marks_obtained = scoring.outcome(question)      # the ONE scorer: the same marks as the stored result
        if status == "UNANSWERED":
            marks_obtained = 0
            subject_data["unanswered_questions"] += 1
        else:
            subject_data["attempted_questions"] += 1
            subject_data["correct_answers" if status == "CORRECT" else "incorrect_answers"] += 1
            subject_data["obtained_marks"] += marks_obtained

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
                # MSQ / NAT: the options or number format and the typed key (shown only once the review is open)
                "question_type": "MCQ" if qtype == "MCQ_SINGLE" else qtype,
                "content": scoring._json(question.get("content")),
                "answer_key": None if qtype == "MCQ_SINGLE" else key,
                "status": status,
                "marks_obtained": marks_obtained,
                "marks": marks,
                "negative_marks": negative_marks,
                "image_reference": question["image_reference"],
                "question_image": question["question_image"],
                "option_a_image": question["option_a_image"],
                "option_b_image": question["option_b_image"],
                "option_c_image": question["option_c_image"],
                "option_d_image": question["option_d_image"],
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
    unattributed = None
    if global_results.is_global_test(db, result["test_id"]):
        if global_results.has_paper_choice(sections):            # the student's own sections (their papers, or why none)
            sections = global_results.student_sections(db, sections, result["test_id"], student_id, global_results.snapshot(db, attempt_id))
        # each subject's marks include its attributed manual adjustment
        deltas = global_results.deltas(db, result["result_id"], result["subject_adjustments"])
        for x in subject_performance:
            x["adjustment"] = (deltas or {}).get(x["subject"], Decimal(0))
            x["obtained_marks"] += x["adjustment"]
        if deltas is None:              # never guessed: the official total minus the scored subjects, shown on its own line
            unattributed = to_decimal(result["obtained_marks"]) - sum((x["obtained_marks"] for x in subject_performance), Decimal(0))
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
    elif _shuffled(db, result["test_id"]):          # the review lists the questions in the order this student sat them
        question_reviews = section_order(question_reviews, None, student_id, result["test_id"],
                                         subject_of=lambda q: q["subject"], id_of=lambda q: q["question_id"], shuffle=True)

    return {
        "result": result,
        "subjects": subject_performance,
        "questions": question_reviews if review_available else [],
        "review_available": review_available,
        "review_available_at": None if review_available or review is None else review["end_at"],
        "unattributed_adjustment": unattributed,
    }