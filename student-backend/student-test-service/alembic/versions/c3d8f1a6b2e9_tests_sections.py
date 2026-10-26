"""tests.sections: every configured subject of a unified Global Test exam (additive, nullable)

[{position, subject, status, message, question_count}] in the configured order: the subject's delivered questions, or why the student
has no paper for it. NULL for every other test (Daily/Weekly and older exams keep today's behaviour).

Revision ID: c3d8f1a6b2e9
Revises: a1c9e5d2f7b4
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3d8f1a6b2e9"
down_revision = "a1c9e5d2f7b4"
branch_labels = None
depends_on = None


def upgrade():
    # jsonb on PostgreSQL: the student listings use SELECT DISTINCT, and plain json has no equality operator
    op.add_column("tests", sa.Column("sections", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade():
    op.drop_column("tests", "sections")
