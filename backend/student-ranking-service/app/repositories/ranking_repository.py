from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_test_rankings(
    db: Session,
    test_id: UUID,
):
    query = text("""
        SELECT
            r.student_id,
            COALESCE(
                NULLIF(
                    TRIM(
                        CONCAT_WS(
                            ' ',
                            s.first_name,
                            s.last_name
                        )
                    ),
                    ''
                ),
                r.student_id
            ) AS student_name,
            r.obtained_marks
        FROM test_results r
        LEFT JOIN students s
            ON s.student_id = r.student_id
        WHERE r.test_id = :test_id
        ORDER BY
            r.obtained_marks DESC,
            r.created_at ASC
    """)

    return db.execute(
        query,
        {"test_id": test_id},
    ).mappings().all()