"""add branches and link batches to branches

Revision ID: eb38c100dddd
Revises: 1ea4b579e508
Create Date: 2026-10-01 12:55:03.529236
"""

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "eb38c100dddd"
down_revision: Union[str, Sequence[str], None] = "1ea4b579e508"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "address",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "city",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "state",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "pincode",
            sa.String(length=10),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "INACTIVE",
                name="branch_status",
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
            sa.Numeric(10, 7),
            nullable=True,
        ),
        sa.Column(
            "deleted_longitude",
            sa.Numeric(10, 7),
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
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.provider_id"],
            name="fk_branches_provider_id",
        ),
        sa.PrimaryKeyConstraint("branch_id"),
    )

    connection = op.get_bind()

    providers = connection.execute(
        sa.text(
            """
            SELECT provider_id
            FROM providers
            """
        )
    ).fetchall()

    for provider in providers:
        branch_id = str(uuid4())

        connection.execute(
            sa.text(
                """
                INSERT INTO branches (
                    branch_id,
                    provider_id,
                    name,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (
                    :branch_id,
                    :provider_id,
                    :name,
                    'ACTIVE',
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "branch_id": branch_id,
                "provider_id": provider.provider_id,
                "name": "Main Branch",
            },
        )

    op.add_column(
        "batches",
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
    )

    connection.execute(
        sa.text(
            """
            UPDATE batches b
            SET branch_id = br.branch_id
            FROM branches br
            WHERE b.provider_id = br.provider_id
            """
        )
    )

    op.drop_constraint(
        "batches_provider_id_fkey",
        "batches",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_batches_branch_id",
        "batches",
        "branches",
        ["branch_id"],
        ["branch_id"],
    )

    op.alter_column(
        "batches",
        "branch_id",
        nullable=False,
    )

    op.drop_column(
        "batches",
        "provider_id",
    )


def downgrade() -> None:
    op.add_column(
        "batches",
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
    )

    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            UPDATE batches b
            SET provider_id = br.provider_id
            FROM branches br
            WHERE b.branch_id = br.branch_id
            """
        )
    )

    op.drop_constraint(
        "fk_batches_branch_id",
        "batches",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "batches_provider_id_fkey",
        "batches",
        "providers",
        ["provider_id"],
        ["provider_id"],
    )

    op.alter_column(
        "batches",
        "provider_id",
        nullable=False,
    )

    op.drop_column(
        "batches",
        "branch_id",
    )

    op.drop_constraint(
        "fk_branches_provider_id",
        "branches",
        type_="foreignkey",
    )

    op.drop_table(
        "branches",
    )