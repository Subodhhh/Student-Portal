from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

STUDENT_NAME = """
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
            )"""

# Official results only: once an authorized retake (exam integrity) is evaluated, it replaces its original in every ranking.
OFFICIAL = """NOT EXISTS (SELECT 1 FROM test_attempts ra JOIN test_results rr ON rr.attempt_id = ra.attempt_id
                          WHERE ra.retake_of_attempt_id = r.attempt_id)"""


def get_test_rankings(
    db: Session,
    test_id: UUID,
):
    query = text(f"""
        SELECT
            r.student_id,
            {STUDENT_NAME} AS student_name,
            r.obtained_marks
        FROM test_results r
        LEFT JOIN students s
            ON s.student_id = r.student_id
        WHERE r.test_id = :test_id
          AND {OFFICIAL}
        ORDER BY
            r.obtained_marks DESC,
            r.created_at ASC
    """)

    return db.execute(
        query,
        {"test_id": test_id},
    ).mappings().all()


def get_global_occurrence(
    db: Session,
    test_id: UUID,
):
    """The unified Global Test occurrence of a Student test (NULL for Daily/Weekly and other tests) and its configured subjects in
    order (tests.sections: every test of an occurrence carries the same configured subjects)."""
    return db.execute(
        text("SELECT global_occurrence_id, sections FROM tests WHERE test_id = :test_id"),
        {"test_id": test_id},
    ).mappings().first()


def get_occurrence_results(
    db: Session,
    occurrence_id: UUID,
):
    """Every evaluated result of the occurrence, across all of its Student tests: the official overall marks and the maximum marks of
    the questions actually delivered to that student."""
    query = text(f"""
        SELECT
            r.student_id,
            {STUDENT_NAME} AS student_name,
            r.obtained_marks,
            r.total_marks
        FROM test_results r
        JOIN tests t
            ON t.test_id = r.test_id
        LEFT JOIN students s
            ON s.student_id = r.student_id
        WHERE t.global_occurrence_id = :occurrence_id
          AND {OFFICIAL}
    """)

    return db.execute(
        query,
        {"occurrence_id": occurrence_id},
    ).mappings().all()


def get_occurrence_subject_scores(
    db: Session,
    occurrence_id: UUID,
):
    """Per student and subject: the delivered maximum marks and the marks obtained, scored from the latest saved answer of each
    question with the result service's rules (+marks right, -negative marks wrong, unanswered 0, case/space-insensitive), only on the
    attempt's snapshot papers when the student chose papers, plus the subject's attributed manual adjustment; a result whose adjustment
    is unattributed is left out (its official overall marks still rank in the overall ranking)."""
    query = text(f"""
        WITH scored AS (
            SELECT r.student_id, r.attempt_id, r.test_id, r.subject_adjustments
            FROM test_results r
            JOIN tests t
                ON t.test_id = r.test_id
            WHERE t.global_occurrence_id = :occurrence_id
              AND {OFFICIAL}
              -- a manual adjustment never attributed to a subject keeps the result out of subject rankings (never guessed)
              AND NOT (r.subject_adjustments IS NULL
                       AND EXISTS (SELECT 1 FROM result_history h WHERE h.result_id = r.result_id AND h.kind = 'MARKS_ADJUSTMENT'))
        ),
        latest_answers AS (
            SELECT DISTINCT ON (aa.attempt_id, aa.question_id)
                aa.attempt_id,
                aa.question_id,
                aa.selected_answer
            FROM attempt_answers aa
            JOIN scored sc
                ON sc.attempt_id = aa.attempt_id
            ORDER BY
                aa.attempt_id,
                aa.question_id,
                aa.updated_at DESC,
                aa.answered_at DESC,
                aa.answer_id DESC
        )
        SELECT
            sc.student_id,
            q.subject,
            SUM(COALESCE(q.marks, 0)) AS max_marks,
            SUM(
                CASE
                    WHEN COALESCE(la.selected_answer, '') = '' THEN 0
                    WHEN UPPER(TRIM(la.selected_answer)) = UPPER(TRIM(q.correct_answer)) THEN COALESCE(q.marks, 0)
                    ELSE -COALESCE(q.negative_marks, 0)
                END
            ) + COALESCE(MAX((sc.subject_adjustments ->> q.subject)::int), 0) AS obtained_marks
        FROM scored sc
        JOIN questions q
            ON q.test_id = sc.test_id
            -- with paper choice only the attempt's snapshot papers (what was delivered); every question of its test otherwise
            AND (NOT EXISTS (SELECT 1 FROM attempt_selected_papers sp WHERE sp.attempt_id = sc.attempt_id)
                 OR q.paper_id IN (SELECT sp.paper_id FROM attempt_selected_papers sp WHERE sp.attempt_id = sc.attempt_id))
        LEFT JOIN latest_answers la
            ON la.attempt_id = sc.attempt_id
            AND la.question_id = q.question_id
        GROUP BY
            sc.student_id,
            q.subject
    """)

    return db.execute(
        query,
        {"occurrence_id": occurrence_id},
    ).mappings().all()
