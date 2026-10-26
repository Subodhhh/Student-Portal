"""Exam integrity: recorded browser warnings, automatic submission at a threshold, review requests and authorized retakes (additive)

  * test_attempts: submit_reason (STUDENT / TIME_EXPIRED / WARNING_THRESHOLD; NULL for attempts before this revision), review_status
    (UNDER_REVIEW / RETAKE_AUTHORIZED / UPHELD, warning auto-submissions only) and retake_of_attempt_id (an authorized retake's original).
  * One ordinary attempt per student and test is still enforced, now as UNIQUE(student_id, test_id) WHERE retake_of_attempt_id IS NULL;
    UNIQUE(retake_of_attempt_id) allows at most one retake per original. Only the internal (service-key) API creates a retake.
  * attempt_integrity_events: every reported browser event (tab hidden, window blur, fullscreen exit), whether it counted as a warning
    (server-side de-duplication) and its warning number; client_event_id makes a retried report idempotent.
  * attempt_reviews: the student's review request for a warning auto-submission and the provider's decision (who, when, why) with the
    linked retake. Nothing here ever changes the original attempt's answers, events or result.

Revision ID: attempt_integrity_reviews
Revises: add_question_state
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "attempt_integrity_reviews"
down_revision = "add_question_state"
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade():
    op.add_column("test_attempts", sa.Column("submit_reason", sa.String(30), nullable=True))
    op.add_column("test_attempts", sa.Column("review_status", sa.String(20), nullable=True))
    op.add_column("test_attempts", sa.Column("retake_of_attempt_id", postgresql.UUID(as_uuid=True),
                                             sa.ForeignKey("test_attempts.attempt_id"), nullable=True))
    op.create_check_constraint("ck_test_attempts_submit_reason", "test_attempts",
                               "submit_reason IS NULL OR submit_reason IN ('STUDENT', 'TIME_EXPIRED', 'WARNING_THRESHOLD')")
    op.create_check_constraint("ck_test_attempts_review_status", "test_attempts",
                               "review_status IS NULL OR review_status IN ('UNDER_REVIEW', 'RETAKE_AUTHORIZED', 'UPHELD')")
    op.drop_constraint("uq_test_attempts_student_test", "test_attempts", type_="unique")
    op.create_index("uq_test_attempts_student_test", "test_attempts", ["student_id", "test_id"], unique=True,
                    postgresql_where=sa.text("retake_of_attempt_id IS NULL"))
    op.create_index("uq_test_attempts_retake_of", "test_attempts", ["retake_of_attempt_id"], unique=True,
                    postgresql_where=sa.text("retake_of_attempt_id IS NOT NULL"))

    op.create_table(
        "attempt_integrity_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_attempts.attempt_id"), nullable=False),
        sa.Column("client_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("client_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("counted", sa.Boolean(), nullable=False),
        sa.Column("warning_no", sa.Integer(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("kind IN ('TAB_HIDDEN', 'WINDOW_BLUR', 'FULLSCREEN_EXIT')", name="ck_integrity_events_kind"),
        sa.CheckConstraint("counted = (warning_no IS NOT NULL)", name="ck_integrity_events_counted"),
        sa.UniqueConstraint("attempt_id", "client_event_id", name="uq_integrity_events_client_event"),
        sa.UniqueConstraint("attempt_id", "warning_no", name="uq_integrity_events_warning_no"),
    )
    op.create_index("ix_integrity_events_attempt", "attempt_integrity_events", ["attempt_id", "received_at"])

    op.create_table(
        "attempt_reviews",
        sa.Column("review_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_attempts.attempt_id"), nullable=False),
        sa.Column("student_id", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("student_message", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("decided_by_staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by_name", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("retake_attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_attempts.attempt_id"), nullable=True),
        sa.CheckConstraint("status IN ('OPEN', 'RETAKE_AUTHORIZED', 'UPHELD')", name="ck_attempt_reviews_status"),
        sa.CheckConstraint("(status = 'OPEN') = (decided_at IS NULL)", name="ck_attempt_reviews_decided"),
        sa.UniqueConstraint("attempt_id", name="uq_attempt_reviews_attempt"),          # one review per auto-submitted attempt
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM test_attempts WHERE retake_of_attempt_id IS NOT NULL")).scalar():
        raise RuntimeError("authorized retakes exist: the one-attempt unique constraint cannot be restored without deleting them")
    op.drop_table("attempt_reviews")
    op.drop_index("ix_integrity_events_attempt", table_name="attempt_integrity_events")
    op.drop_table("attempt_integrity_events")
    op.drop_index("uq_test_attempts_retake_of", table_name="test_attempts")
    op.drop_index("uq_test_attempts_student_test", table_name="test_attempts")
    op.create_unique_constraint("uq_test_attempts_student_test", "test_attempts", ["student_id", "test_id"])
    op.drop_constraint("ck_test_attempts_review_status", "test_attempts", type_="check")
    op.drop_constraint("ck_test_attempts_submit_reason", "test_attempts", type_="check")
    op.drop_column("test_attempts", "retake_of_attempt_id")
    op.drop_column("test_attempts", "review_status")
    op.drop_column("test_attempts", "submit_reason")
