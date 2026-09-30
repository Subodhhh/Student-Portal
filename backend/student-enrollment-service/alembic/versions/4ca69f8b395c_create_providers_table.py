"""create providers table

Revision ID: 4ca69f8b395c
Revises:
Create Date: 2026-09-30 12:15:39.487620
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "4ca69f8b395c"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column(
            "provider_id",
            sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "email",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "phone",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column(
            "address",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "INACTIVE",
                name="provider_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_ip_address",
            postgresql.INET(),
            nullable=True,
        ),
        sa.Column(
            "created_latitude",
            sa.Numeric(precision=10, scale=7),
            nullable=True,
        ),
        sa.Column(
            "created_longitude",
            sa.Numeric(precision=10, scale=7),
            nullable=True,
        ),
        sa.Column(
            "updated_ip_address",
            postgresql.INET(),
            nullable=True,
        ),
        sa.Column(
            "updated_latitude",
            sa.Numeric(precision=10, scale=7),
            nullable=True,
        ),
        sa.Column(
            "updated_longitude",
            sa.Numeric(precision=10, scale=7),
            nullable=True,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "deleted_ip_address",
            postgresql.INET(),
            nullable=True,
        ),
        sa.Column(
            "deleted_latitude",
            sa.Numeric(precision=10, scale=7),
            nullable=True,
        ),
        sa.Column(
            "deleted_longitude",
            sa.Numeric(precision=10, scale=7),
            nullable=True,
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
        sa.PrimaryKeyConstraint("provider_id"),
        sa.UniqueConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("providers")