"""Decimal marks: test_results.total_marks / obtained_marks INTEGER -> NUMERIC(8,2)

Marks may now have up to 2 decimal places (0.25, 1.5, ...). The cast is lossless: every existing whole value is unchanged.
The downgrade refuses to run while any fractional mark exists, because turning it back into an integer would silently change
students' marks.

Revision ID: f5c0b7d2e9a4
Revises: d9b3f5a1c7e2
"""

import sqlalchemy as sa
from alembic import op

revision = "f5c0b7d2e9a4"
down_revision = "d9b3f5a1c7e2"
branch_labels = None
depends_on = None

COLUMNS = [("test_results", "total_marks", 8), ("test_results", "obtained_marks", 8)]


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
