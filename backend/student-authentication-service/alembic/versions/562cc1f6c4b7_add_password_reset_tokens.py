"""add password reset tokens

Revision ID: 562cc1f6c4b7
Revises: 7ada309b2310
Create Date: 2026-09-28 08:58:33.752141
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "562cc1f6c4b7"
down_revision: Union[str, Sequence[str], None] = "7ada309b2310"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_setup_reset_tokens",
        sa.Column(
            "token_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "token_type",
            sa.String(length=30),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "created_ip_address",
            postgresql.INET(),
            nullable=True,
        ),
        sa.Column(
            "created_latitude",
            sa.Numeric(10, 7),
            nullable=True,
        ),
        sa.Column(
            "created_longitude",
            sa.Numeric(10, 7),
            nullable=True,
        ),
        sa.Column(
            "updated_ip_address",
            postgresql.INET(),
            nullable=True,
        ),
        sa.Column(
            "updated_latitude",
            sa.Numeric(10, 7),
            nullable=True,
        ),
        sa.Column(
            "updated_longitude",
            sa.Numeric(10, 7),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.student_id"],
        ),
        sa.PrimaryKeyConstraint("token_id"),
        sa.CheckConstraint(
            "token_type IN ('PASSWORD_SETUP', 'PASSWORD_RESET')",
            name="password_setup_reset_tokens_type_check",
        ),
    )


def downgrade() -> None:
    op.drop_table("password_setup_reset_tokens")