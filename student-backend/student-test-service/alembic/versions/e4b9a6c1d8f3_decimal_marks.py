"""Decimal marks: questions.marks / negative_marks INTEGER -> NUMERIC(6,2), tests.total_marks INTEGER -> NUMERIC(8,2)

Marks may now have up to 2 decimal places (0.25, 1.5, ...). The cast is lossless: every existing whole value is unchanged.
The downgrade refuses to run while any fractional mark exists, because turning it back into an integer would silently change
students' marks.

Revision ID: e4b9a6c1d8f3
Revises: b7e3d1f9a2c6
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b9a6c1d8f3"
down_revision = "b7e3d1f9a2c6"
branch_labels = None
depends_on = None

COLUMNS = [("questions", "marks", 6), ("questions", "negative_marks", 6), ("tests", "total_marks", 8)]


def upgrade():
    for table, column, precision in COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE NUMERIC({precision},2) USING {column}::numeric")


def downgrade():
    bind = op.get_bind()
    for table, column, _ in COLUMNS:
        if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE {column} <> trunc({column}) LIMIT 1")).first():
            raise RuntimeError(f"Cannot downgrade: {table}.{column} holds fractional marks that an INTEGER column would truncate")
    for table, column, _ in COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE INTEGER USING {column}::integer")
