from alembic import op
import sqlalchemy as sa


revision = "make_attempt_timestamps_tz"
down_revision = "fe7584138b98"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "test_attempts",
        "started_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="started_at AT TIME ZONE 'UTC'",
    )

    op.alter_column(
        "test_attempts",
        "expires_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="expires_at AT TIME ZONE 'UTC'",
    )

    op.alter_column(
        "test_attempts",
        "submitted_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="submitted_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "test_attempts",
        "started_at",
        type_=sa.DateTime(),
    )

    op.alter_column(
        "test_attempts",
        "expires_at",
        type_=sa.DateTime(),
    )

    op.alter_column(
        "test_attempts",
        "submitted_at",
        type_=sa.DateTime()
    )