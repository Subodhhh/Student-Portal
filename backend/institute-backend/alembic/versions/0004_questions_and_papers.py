"""question bank, papers, versions, uploads, files, sharing (additive)

Revision ID: 0004_papers
Revises: 0003_enrollment_history
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_papers"
down_revision = "0003_enrollment_history"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("CURRENT_TIMESTAMP")
INET = postgresql.INET().with_variant(sa.String(45), "sqlite")
JSONT = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("file_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_stored_files_provider_id", "stored_files", ["provider_id"])
    # `bank_questions`, not `questions`: the Student test service already owns `questions`.
    op.create_table(
        "bank_questions",
        sa.Column("question_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("owner_staff_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
    )
    op.create_index("ix_bank_questions_provider_id", "bank_questions", ["provider_id"])
    op.create_table(
        "question_versions",
        sa.Column("question_version_id", sa.Uuid(), primary_key=True),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("bank_questions.question_id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("option_a", sa.Text(), nullable=False),
        sa.Column("option_b", sa.Text(), nullable=False),
        sa.Column("option_c", sa.Text(), nullable=False),
        sa.Column("option_d", sa.Text(), nullable=False),
        sa.Column("correct_option", sa.String(1), nullable=False),
        sa.Column("explanation", sa.Text()),
        sa.Column("topic", sa.String(200)),
        sa.Column("marks_positive", sa.Numeric(6, 2), nullable=False),
        sa.Column("marks_negative", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("question_id", "version_no", name="uq_question_version"),
        sa.CheckConstraint("correct_option IN ('A','B','C','D')", name="question_versions_correct_check"),
        sa.CheckConstraint("marks_positive > 0 AND marks_negative >= 0", name="question_versions_marks_check"),
    )
    op.create_index("ix_question_versions_question_id", "question_versions", ["question_id"])
    op.create_table(
        "papers",
        sa.Column("paper_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.branch_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
    )
    op.create_index("ix_papers_provider_id", "papers", ["provider_id"])
    op.create_table(
        "paper_versions",
        sa.Column("paper_version_id", sa.Uuid(), primary_key=True),
        sa.Column("paper_id", sa.Uuid(), sa.ForeignKey("papers.paper_id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="VALIDATED"),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("total_marks", sa.Numeric(8, 2), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("published_at", TS),
        sa.UniqueConstraint("paper_id", "version_no", name="uq_paper_version"),
        sa.CheckConstraint("status IN ('VALIDATED','APPROVED','PUBLISHED','SUPERSEDED','WITHDRAWN')", name="paper_versions_status_check"),
    )
    op.create_index("ix_paper_versions_paper_id", "paper_versions", ["paper_id"])
    op.create_table(
        "paper_version_questions",
        sa.Column("pvq_id", sa.Uuid(), primary_key=True),
        sa.Column("paper_version_id", sa.Uuid(), sa.ForeignKey("paper_versions.paper_version_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), sa.ForeignKey("question_versions.question_version_id"), nullable=False),
        sa.UniqueConstraint("paper_version_id", "position", name="uq_pvq_position"),
    )
    op.create_index("ix_paper_version_questions_paper_version_id", "paper_version_questions", ["paper_version_id"])
    op.create_table(
        "paper_uploads",
        sa.Column("upload_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("stored_files.file_id"), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("question_count", sa.Integer()),
        sa.Column("errors", JSONT),
        sa.Column("paper_version_id", sa.Uuid()),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("uploaded_ip", INET),
        sa.CheckConstraint("status IN ('VALIDATED','REJECTED')", name="paper_uploads_status_check"),
    )
    op.create_index("ix_paper_uploads_provider_id", "paper_uploads", ["provider_id"])
    op.create_table(
        "paper_version_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("paper_version_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20)),
        sa.Column("actor_staff_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("occurred_at", TS, nullable=False, server_default=NOW),
    )
    op.create_index("ix_paper_version_events_paper_version_id", "paper_version_events", ["paper_version_id"])
    op.create_table(
        "paper_shares",
        sa.Column("share_id", sa.Uuid(), primary_key=True),
        sa.Column("paper_id", sa.Uuid(), sa.ForeignKey("papers.paper_id"), nullable=False),
        sa.Column("owner_provider_id", sa.Uuid(), nullable=False),
        sa.Column("shared_with_provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("paper_id", "shared_with_provider_id", name="uq_paper_share"),
    )
    op.create_index("ix_paper_shares_shared_with_provider_id", "paper_shares", ["shared_with_provider_id"])
    if op.get_bind().dialect.name == "postgresql":
        # Published history must not be rewritten by the application role.
        op.execute("""
            CREATE FUNCTION institute_history_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
            $$ LANGUAGE plpgsql;
        """)
        for t in ("paper_version_events", "question_versions", "paper_version_questions"):
            op.execute(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE OR DELETE ON {t} "
                       "FOR EACH ROW EXECUTE FUNCTION institute_history_immutable();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in ("paper_version_events", "question_versions", "paper_version_questions"):
            op.execute(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
        op.execute("DROP FUNCTION IF EXISTS institute_history_immutable()")
    for t in ("paper_shares", "paper_version_events", "paper_uploads", "paper_version_questions",
              "paper_versions", "papers", "question_versions", "bank_questions", "stored_files"):
        op.drop_table(t)
