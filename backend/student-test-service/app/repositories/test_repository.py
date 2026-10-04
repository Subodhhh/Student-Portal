from sqlalchemy import text
from sqlalchemy.orm import Session


def get_test_by_id(
    db: Session,
    test_id,
    student_id: str,
):
    query = text(
        """
        SELECT DISTINCT
            t.test_id,
            t.name,
            t.description,
            t.duration_minutes,
            t.total_questions,
            t.total_marks,
            t.start_at,
            t.end_at,
            t.status,

            p.provider_id,
            p.name AS provider_name,

            b.branch_id,
            b.name AS branch_name,
            b.city,
            b.state

        FROM tests t

        JOIN providers p
            ON t.provider_id = p.provider_id

        JOIN branches b
            ON t.branch_id = b.branch_id

        JOIN test_audiences ta
            ON ta.test_id = t.test_id

        LEFT JOIN enrollments e
            ON e.student_id = :student_id
            AND e.status = 'ACTIVE'

        LEFT JOIN enrollment_batches eb
            ON eb.enrollment_id = e.enrollment_id

        WHERE t.test_id = :test_id
          AND t.status = 'ACTIVE'
          AND (
                (
                    ta.audience_type = 'PROVIDER'
                    AND ta.provider_id = e.provider_id
                )
                OR
                (
                    ta.audience_type = 'BRANCH'
                    AND ta.provider_id = e.provider_id
                    AND ta.branch_id = e.branch_id
                )
                OR
                (
                    ta.audience_type = 'BATCH'
                    AND ta.provider_id = e.provider_id
                    AND ta.batch_id = eb.batch_id
                )
                OR
                (
                    ta.audience_type = 'STUDENT'
                    AND ta.student_id = :student_id
                )
          )
        """
    )

    return db.execute(
        query,
        {
            "test_id": test_id,
            "student_id": student_id,
        },
    ).mappings().first()


def get_active_tests(
    db: Session,
    student_id: str,
):
    query = text(
        """
        SELECT DISTINCT
            t.test_id,
            t.name,
            t.description,
            t.duration_minutes,
            t.total_questions,
            t.total_marks,
            t.start_at,
            t.end_at,
            t.status,

            p.provider_id,
            p.name AS provider_name,

            b.branch_id,
            b.name AS branch_name,
            b.city,
            b.state

        FROM tests t

        JOIN providers p
            ON t.provider_id = p.provider_id

        JOIN branches b
            ON t.branch_id = b.branch_id

        JOIN test_audiences ta
            ON ta.test_id = t.test_id

        LEFT JOIN enrollments e
            ON e.student_id = :student_id
            AND e.status = 'ACTIVE'

        LEFT JOIN enrollment_batches eb
            ON eb.enrollment_id = e.enrollment_id

        WHERE t.status = 'ACTIVE'
          AND (
                (
                    ta.audience_type = 'PROVIDER'
                    AND ta.provider_id = e.provider_id
                )
                OR
                (
                    ta.audience_type = 'BRANCH'
                    AND ta.provider_id = e.provider_id
                    AND ta.branch_id = e.branch_id
                )
                OR
                (
                    ta.audience_type = 'BATCH'
                    AND ta.provider_id = e.provider_id
                    AND ta.batch_id = eb.batch_id
                )
                OR
                (
                    ta.audience_type = 'STUDENT'
                    AND ta.student_id = :student_id
                )
          )

        ORDER BY t.start_at ASC
        """
    )

    return db.execute(
        query,
        {
            "student_id": student_id,
        },
    ).mappings().all()