"""test_results.subject_adjustments: a Global Test result's manual adjustment attributed per subject (additive, nullable)

{subject: delta}; obtained_marks already includes the deltas (never added again). NULL for every existing result: an older
MARKS_ADJUSTMENT stays unattributed (nothing is guessed).

Revision ID: c6f1d9a3b5e8
Revises: b2d4e1a9c7f3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6f1d9a3b5e8"
down_revision: Union[str, Sequence[str], None] = "b2d4e1a9c7f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_results", sa.Column("subject_adjustments", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade() -> None:
    op.drop_column("test_results", "subject_adjustments")
