from alembic import op


revision = "add_unique_student_test_attempt"
down_revision = "make_attempt_timestamps_tz"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_test_attempts_student_test",
        "test_attempts",
        ["student_id", "test_id"],
    )


def downgrade():
    op.drop_constraint(
        "uq_test_attempts_student_test",
        "test_attempts",
        type_="unique",
    )