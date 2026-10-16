"""create tests table

Revision ID: b36306966afb
Revises:
Create Date: 2026-10-01 15:10:27.449964
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b36306966afb"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tests",
        sa.Column(
            "test_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "duration_minutes",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "total_questions",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "total_marks",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "start_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "end_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "PUBLISHED",
                "ACTIVE",
                "INACTIVE",
                "COMPLETED",
                name="test_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.provider_id"],
            name="fk_tests_provider_id",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.branch_id"],
            name="fk_tests_branch_id",
        ),
        sa.PrimaryKeyConstraint("test_id"),
    )


def downgrade() -> None:
    op.drop_table("tests")