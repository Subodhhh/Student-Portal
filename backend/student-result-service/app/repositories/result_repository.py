from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.test_result import TestResult


def get_attempt(db: Session, attempt_id: UUID, student_id: str):
    query = text("""
        SELECT
            attempt_id,
            test_id,
            student_id,
            status
        FROM test_attempts
        WHERE attempt_id = :attempt_id
          AND student_id = :student_id
    """)

    return db.execute(
        query,
        {
            "attempt_id": attempt_id,
            "student_id": student_id,
        },
    ).mappings().first()


def get_existing_result(
    db: Session,
    attempt_id: UUID,
    student_id: str,
):
    query = text("""
        SELECT *
        FROM test_results
        WHERE attempt_id = :attempt_id
          AND student_id = :student_id
    """)

    return db.execute(
        query,
        {
            "attempt_id": attempt_id,
            "student_id": student_id,
        },
    ).mappings().first()


def get_questions_with_answers(
    db: Session,
    attempt_id: UUID,
):
    query = text("""
        WITH latest_answers AS (
            SELECT DISTINCT ON (attempt_id, question_id)
                attempt_id,
                question_id,
                selected_answer
            FROM attempt_answers
            WHERE attempt_id = :attempt_id
            ORDER BY
                attempt_id,
                question_id,
                updated_at DESC,
                answered_at DESC,
                answer_id DESC
        )
        SELECT
            q.question_id,
            q.question_order,
            q.subject,
            q.question_text,
            q.question_type,
            q.option_a,
            q.option_b,
            q.option_c,
            q.option_d,
            q.correct_answer,
            q.marks,
            q.negative_marks,
            q.image_reference,
            la.selected_answer
        FROM questions q
        LEFT JOIN latest_answers la
            ON la.question_id = q.question_id
        WHERE q.test_id = (
            SELECT test_id
            FROM test_attempts
            WHERE attempt_id = :attempt_id
        )
        ORDER BY q.question_order
    """)

    return db.execute(
        query,
        {"attempt_id": attempt_id},
    ).mappings().all()


def create_result(db: Session, result: TestResult):
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def get_student_results(db: Session, student_id: str):
    query = text("""
        SELECT
            r.result_id,
            r.attempt_id,
            r.test_id,
            r.student_id,
            r.total_questions,
            r.attempted_questions,
            r.correct_answers,
            r.incorrect_answers,
            r.unanswered_questions,
            r.total_marks,
            r.obtained_marks,
            r.accuracy,
            r.created_at,

            t.name AS test_name,
            t.duration_minutes,

            p.provider_id,
            p.name AS provider_name,

            b.branch_id,
            b.name AS branch_name,
            b.city,
            b.state

        FROM test_results r

        JOIN tests t
            ON t.test_id = r.test_id

        JOIN providers p
            ON p.provider_id = t.provider_id

        JOIN branches b
            ON b.branch_id = t.branch_id

        WHERE r.student_id = :student_id

        ORDER BY r.created_at DESC
    """)

    return db.execute(
        query,
        {"student_id": student_id},
    ).mappings().all()

def get_review_window(db: Session, test_id):
    """Whether question-level answer review may be shown for a test: after its availability window (end_at, compared with
    NOW() in the session time zone like every other test query). end_at is returned as an aware UTC instant."""
    return db.execute(
        text("""
            SELECT (end_at IS NULL OR NOW() > end_at) AS open,
                   CASE WHEN end_at IS NULL THEN NULL
                        ELSE (end_at AT TIME ZONE current_setting('TimeZone')) END AS end_at
            FROM tests
            WHERE test_id = :test_id
        """),
        {"test_id": test_id},
    ).mappings().first()


def get_unevaluated_attempts(db: Session, limit: int = 100):
    return db.execute(
        text("""
            SELECT a.attempt_id, a.student_id
            FROM test_attempts a
            LEFT JOIN test_results r ON r.attempt_id = a.attempt_id
            WHERE a.status IN ('SUBMITTED', 'AUTO_SUBMITTED')
              AND r.result_id IS NULL
            ORDER BY a.submitted_at NULLS FIRST
            LIMIT :limit
        """),
        {"limit": limit},
    ).mappings().all()
