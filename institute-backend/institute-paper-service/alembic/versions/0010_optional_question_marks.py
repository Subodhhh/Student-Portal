"""Optional question marks (marks belong to the test)

  * question_versions.marks_positive / marks_negative become nullable and lose their defaults; `question_versions_marks_check`
    becomes "both NULL, or both set with marks_positive > 0 AND marks_negative >= 0". A question without marks gets them from the test
    (RULES or UNIFORM marking).
  * paper_versions.total_marks becomes nullable: NULL when a paper contains a question without marks.

No row is changed: every existing version has marks and every existing paper has a total, which the new rules accept.
Dropping NOT NULL and a default only changes the catalogue in PostgreSQL (no table rewrite).

PostgreSQL only (DROP CONSTRAINT IF EXISTS), like the constraint changes of the other chains.

Revision ID: 0010_optional_question_marks
Revises: 0009_picture_recognition_jobs
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_optional_question_marks"
down_revision = "0009_picture_recognition_jobs"
branch_labels = None
depends_on = None

CHECK = "question_versions_marks_check"
# The IS NOT NULLs matter: a CHECK whose condition is NULL passes, so "marks_positive > 0" alone would let one of the two be NULL.
NEW_RULE = ("(marks_positive IS NULL AND marks_negative IS NULL) OR "
            "(marks_positive IS NOT NULL AND marks_negative IS NOT NULL AND marks_positive > 0 AND marks_negative >= 0)")
OLD_RULE = "marks_positive > 0 AND marks_negative >= 0"


def upgrade() -> None:
    op.execute("ALTER TABLE question_versions ALTER COLUMN marks_positive DROP NOT NULL")
    op.execute("ALTER TABLE question_versions ALTER COLUMN marks_negative DROP NOT NULL")
    # A default of 0 for only one of the two would break "both or neither"; every insert passes both values explicitly.
    op.execute("ALTER TABLE question_versions ALTER COLUMN marks_negative DROP DEFAULT")
    op.execute(f'ALTER TABLE question_versions DROP CONSTRAINT IF EXISTS "{CHECK}"')
    op.execute(f'ALTER TABLE question_versions ADD CONSTRAINT "{CHECK}" CHECK ({NEW_RULE})')
    op.execute("ALTER TABLE paper_versions ALTER COLUMN total_marks DROP NOT NULL")


def downgrade() -> None:
    bind = op.get_bind()
    # Refuse rather than invent marks for questions or papers that were created without them.
    unmarked = bind.execute(sa.text("SELECT count(*) FROM question_versions WHERE marks_positive IS NULL")).scalar()
    untotalled = bind.execute(sa.text("SELECT count(*) FROM paper_versions WHERE total_marks IS NULL")).scalar()
    if unmarked or untotalled:
        raise RuntimeError(f"{unmarked} question version(s) and {untotalled} paper version(s) have no marks; "
                           "they cannot be represented before this revision.")
    op.execute("ALTER TABLE paper_versions ALTER COLUMN total_marks SET NOT NULL")
    op.execute(f'ALTER TABLE question_versions DROP CONSTRAINT IF EXISTS "{CHECK}"')
    op.execute(f'ALTER TABLE question_versions ADD CONSTRAINT "{CHECK}" CHECK ({OLD_RULE})')
    op.execute("ALTER TABLE question_versions ALTER COLUMN marks_negative SET DEFAULT 0")
    op.execute("ALTER TABLE question_versions ALTER COLUMN marks_negative SET NOT NULL")
    op.execute("ALTER TABLE question_versions ALTER COLUMN marks_positive SET NOT NULL")
