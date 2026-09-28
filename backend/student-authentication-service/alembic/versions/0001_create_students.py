from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_create_students"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL generates UUIDs for new students.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    status_enum = sa.Enum("ACTIVE", name="student_status")
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "students",
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("last_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default="ACTIVE"),
        sa.Column("created_ip_address", postgresql.INET(), nullable=True),
        sa.Column("created_latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("created_longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("updated_ip_address", postgresql.INET(), nullable=True),
        sa.Column("updated_latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("updated_longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_ip_address", postgresql.INET(), nullable=True),
        sa.Column("deleted_latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("deleted_longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("student_id"),
        sa.UniqueConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("students")
    sa.Enum(name="student_status").drop(op.get_bind(), checkfirst=True)
