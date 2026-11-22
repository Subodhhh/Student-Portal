"""Canonical question taxonomy (additive)

  * question_taxonomy_nodes: a provider's Chapter / Topic / Subtopic records per subject (Subject -> Chapter -> Topic -> Subtopic),
    unique per place in the tree by a normalised key (case, spacing and Unicode width ignored);
  * question_taxonomy_aliases: other spellings an administrator approved for a record;
  * bank_questions gains chapter_id, topic_id, subtopic_id: nullable references to those records.

No existing row is changed: the new columns start NULL and the text labels (chapter, topic, subtopic) stay as they are. Linking
existing questions is a separate, reviewable step (python -m inst_paper.jobs.backfill_taxonomy, dry run by default). A database
created after these joined the models already has them (0001 baseline); they are then adopted only when they have exactly these
columns; any other shape stops the upgrade.

Revision ID: 0011_question_taxonomy
Revises: 0010_optional_question_marks
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import IPAddress
from institute_common.migrations import existing_columns

revision = "0011_question_taxonomy"
down_revision = "0010_optional_question_marks"
branch_labels = None
depends_on = None

NODES, ALIASES = "question_taxonomy_nodes", "question_taxonomy_aliases"
NOW = sa.text("CURRENT_TIMESTAMP")
LINK_COLUMNS = ("chapter_id", "topic_id", "subtopic_id")
LATER_COLUMNS = {NODES: {"merged_into_id"}}       # 0017_taxonomy_aliases


def _node_columns() -> list[sa.Column]:
    return [
        sa.Column("node_id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey(f"{NODES}.node_id")),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_ip", IPAddress),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    ]


def _alias_columns() -> list[sa.Column]:
    return [
        sa.Column("alias_id", sa.Uuid(), primary_key=True),
        sa.Column("node_id", sa.Uuid(), sa.ForeignKey(f"{NODES}.node_id"), nullable=False),
        sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("subjects.subject_id"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey(f"{NODES}.node_id")),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("alias_key", sa.String(200), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("approved_ip", IPAddress),
    ]


def _adopt_or_create(inspector, table: str, columns: list[sa.Column], create) -> None:
    """Create `table`, or accept the one the baseline already made when its columns are exactly these."""
    if table not in inspector.get_table_names():
        create()
        return
    # columns a LATER revision adds are expected on a table the baseline created from today's models
    want, have = {c.name for c in columns}, {c["name"] for c in inspector.get_columns(table)} - LATER_COLUMNS.get(table, set())
    if want != have:
        raise RuntimeError(f"{table} already exists but differs from what 0011_question_taxonomy creates (missing {sorted(want - have)}, "
                           f"unexpected {sorted(have - want)}). Fix the table by hand before upgrading.")


def _create_nodes() -> None:
    op.create_table(
        NODES, *_node_columns(),
        sa.CheckConstraint("level IN ('CHAPTER','TOPIC','SUBTOPIC')", name="question_taxonomy_nodes_level_check"),
        sa.CheckConstraint("(level = 'CHAPTER') = (parent_id IS NULL)", name="question_taxonomy_nodes_parent_check"),
        sa.CheckConstraint("status IN ('ACTIVE','RETIRED')", name="question_taxonomy_nodes_status_check"),
    )
    op.create_index("uq_question_taxonomy_chapter_name", NODES, ["provider_id", "subject_id", "name_key"], unique=True,
                    postgresql_where=sa.text("parent_id IS NULL"))
    op.create_index("uq_question_taxonomy_child_name", NODES, ["parent_id", "name_key"], unique=True,
                    postgresql_where=sa.text("parent_id IS NOT NULL"))
    op.create_index("ix_question_taxonomy_nodes_scope", NODES, ["provider_id", "subject_id", "level"])
    op.create_index("ix_question_taxonomy_nodes_parent_id", NODES, ["parent_id"])


def _create_aliases() -> None:
    op.create_table(ALIASES, *_alias_columns())
    op.create_index("uq_question_taxonomy_chapter_alias", ALIASES, ["provider_id", "subject_id", "alias_key"], unique=True,
                    postgresql_where=sa.text("parent_id IS NULL"))
    op.create_index("uq_question_taxonomy_child_alias", ALIASES, ["parent_id", "alias_key"], unique=True,
                    postgresql_where=sa.text("parent_id IS NOT NULL"))
    op.create_index("ix_question_taxonomy_aliases_node_id", ALIASES, ["node_id"])


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _adopt_or_create(inspector, NODES, _node_columns(), _create_nodes)
    _adopt_or_create(inspector, ALIASES, _alias_columns(), _create_aliases)
    have = existing_columns(bind, "bank_questions")
    for name in LINK_COLUMNS:
        if name in have:
            continue
        with op.batch_alter_table("bank_questions") as batch:
            batch.add_column(sa.Column(name, sa.Uuid(), nullable=True))
            # the name PostgreSQL gives the same key when the table is created from the models (0001 baseline)
            batch.create_foreign_key(f"bank_questions_{name}_fkey", NODES, [name], ["node_id"])


def downgrade() -> None:
    # Only for a database whose taxonomy is not in use: dropping it loses every record, alias and question link.
    bind = op.get_bind()
    in_use = bind.execute(sa.text(f"SELECT count(*) FROM {NODES}")).scalar()
    if in_use:
        raise RuntimeError(f"{in_use} taxonomy record(s) exist; they and their question links would be lost. Downgrade refused.")
    with op.batch_alter_table("bank_questions") as batch:
        for name in LINK_COLUMNS:
            batch.drop_constraint(f"bank_questions_{name}_fkey", type_="foreignkey")
            batch.drop_column(name)
    op.drop_table(ALIASES)
    op.drop_table(NODES)
