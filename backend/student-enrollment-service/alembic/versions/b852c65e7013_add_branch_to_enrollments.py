"""add branch to enrollments

Revision ID: b852c65e7013
Revises: eb38c100dddd
Create Date: 2026-10-01 13:34:48.774854
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b852c65e7013"
down_revision: Union[str, Sequence[str], None] = "eb38c100dddd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "enrollments",
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
    )

    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            UPDATE enrollments e
            SET branch_id = b.branch_id
            FROM branches b
            WHERE e.provider_id = b.provider_id
            """
        )
    )

    op.alter_column(
        "enrollments",
        "branch_id",
        nullable=False,
    )

    op.create_foreign_key(
        "fk_enrollments_branch_id",
        "enrollments",
        "branches",
        ["branch_id"],
        ["branch_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_enrollments_branch_id",
        "enrollments",
        type_="foreignkey",
    )

    op.drop_column(
        "enrollments",
        "branch_id",
    )