"""Chapters, topics & aliases: platform list, merge and alias requests (additive; docs/institute-portal/TAXONOMY_ALIASES_PLAN.md)

  * question_taxonomy_nodes / question_taxonomy_aliases.provider_id become nullable: NULL is the PLATFORM list (decision A3), with
    its own unique indexes (one platform chapter name / alias per subject; topics and subtopics stay unique per parent).
  * question_taxonomy_nodes.merged_into_id and status MERGED: a duplicate folded into another record.
  * question_taxonomy_alias_requests: "remember this spelling" from people who may not approve aliases (decision A2).

Existing rows keep every value. Downgrade refuses while platform records or aliases, merged records or alias requests exist.

Revision ID: 0017_taxonomy_aliases
Revises: 0016_resumable_bulk_uploads
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_taxonomy_aliases"
down_revision = "0016_resumable_bulk_uploads"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    op.execute("ALTER TABLE question_taxonomy_nodes ALTER COLUMN provider_id DROP NOT NULL")
    op.execute("ALTER TABLE question_taxonomy_aliases ALTER COLUMN provider_id DROP NOT NULL")
    if "merged_into_id" not in {c["name"] for c in inspector.get_columns("question_taxonomy_nodes")}:
        op.add_column("question_taxonomy_nodes", sa.Column("merged_into_id", sa.Uuid(), sa.ForeignKey("question_taxonomy_nodes.node_id")))
    op.execute('ALTER TABLE question_taxonomy_nodes DROP CONSTRAINT IF EXISTS "question_taxonomy_nodes_status_check"')
    op.execute("ALTER TABLE question_taxonomy_nodes ADD CONSTRAINT \"question_taxonomy_nodes_status_check\" "
               "CHECK (status IN ('ACTIVE','RETIRED','MERGED'))")
    op.execute('ALTER TABLE question_taxonomy_nodes DROP CONSTRAINT IF EXISTS "question_taxonomy_nodes_merged_check"')
    op.execute("ALTER TABLE question_taxonomy_nodes ADD CONSTRAINT \"question_taxonomy_nodes_merged_check\" "
               "CHECK ((status = 'MERGED') = (merged_into_id IS NOT NULL))")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_question_taxonomy_platform_chapter_name ON question_taxonomy_nodes "
               "(subject_id, name_key) WHERE parent_id IS NULL AND provider_id IS NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_question_taxonomy_platform_chapter_alias ON question_taxonomy_aliases "
               "(subject_id, alias_key) WHERE parent_id IS NULL AND provider_id IS NULL")
    if "question_taxonomy_alias_requests" not in inspector.get_table_names():
        op.create_table(
            "question_taxonomy_alias_requests",
            sa.Column("request_id", sa.Uuid(), primary_key=True),
            sa.Column("node_id", sa.Uuid(), sa.ForeignKey("question_taxonomy_nodes.node_id"), nullable=False),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id")),
            sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
            sa.Column("alias", sa.String(200), nullable=False),
            sa.Column("alias_key", sa.String(200), nullable=False),
            sa.Column("source", sa.String(12), nullable=False),
            sa.Column("source_ref", sa.String(64)),
            sa.Column("status", sa.String(10), nullable=False, server_default="PENDING"),
            sa.Column("requested_by", sa.Uuid(), nullable=False),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
            sa.Column("decided_by", sa.Uuid()),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.Column("decision_reason", sa.String(500)),
            sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="question_taxonomy_alias_requests_status_check"),
            sa.CheckConstraint("source IN ('BULK_UPLOAD','QUESTION','CLASSIFY','UNLINKED')", name="question_taxonomy_alias_requests_source_check"),
        )
        op.create_index("ix_question_taxonomy_alias_requests_owner", "question_taxonomy_alias_requests", ["provider_id", "status", "requested_at"])
        op.execute("CREATE UNIQUE INDEX uq_question_taxonomy_alias_requests_pending ON question_taxonomy_alias_requests (node_id, alias_key) "
                   "WHERE status = 'PENDING'")


def downgrade() -> None:
    bind = op.get_bind()
    count = lambda sql: bind.execute(sa.text(sql)).scalar()     # noqa: E731
    platform = count("SELECT count(*) FROM question_taxonomy_nodes WHERE provider_id IS NULL") + \
        count("SELECT count(*) FROM question_taxonomy_aliases WHERE provider_id IS NULL")
    merged = count("SELECT count(*) FROM question_taxonomy_nodes WHERE status = 'MERGED'")
    requests = count("SELECT count(*) FROM question_taxonomy_alias_requests")
    if platform or merged or requests:
        raise RuntimeError(f"{platform} platform record(s)/alias(es), {merged} merged record(s) and {requests} alias request(s) exist; they "
                           "cannot be represented before this revision. Downgrade refused.")
    op.drop_table("question_taxonomy_alias_requests")
    op.execute("DROP INDEX IF EXISTS uq_question_taxonomy_platform_chapter_alias")
    op.execute("DROP INDEX IF EXISTS uq_question_taxonomy_platform_chapter_name")
    op.execute('ALTER TABLE question_taxonomy_nodes DROP CONSTRAINT IF EXISTS "question_taxonomy_nodes_merged_check"')
    op.execute('ALTER TABLE question_taxonomy_nodes DROP CONSTRAINT IF EXISTS "question_taxonomy_nodes_status_check"')
    op.execute("ALTER TABLE question_taxonomy_nodes ADD CONSTRAINT \"question_taxonomy_nodes_status_check\" CHECK (status IN ('ACTIVE','RETIRED'))")
    op.drop_column("question_taxonomy_nodes", "merged_into_id")
    op.execute("ALTER TABLE question_taxonomy_aliases ALTER COLUMN provider_id SET NOT NULL")
    op.execute("ALTER TABLE question_taxonomy_nodes ALTER COLUMN provider_id SET NOT NULL")
