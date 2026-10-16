"""create result history (immutable record of result corrections)

Revision ID: b2d4e1a9c7f3
Revises: f7ac6d041c89
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2d4e1a9c7f3"
down_revision: Union[str, Sequence[str], None] = "f7ac6d041c89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "result_history",
        sa.Column("history_id", sa.UUID(), nullable=False),
        sa.Column("result_id", sa.UUID(), nullable=False),
        sa.Column("test_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("correction_ref", sa.String(100), nullable=False),
        sa.Column("previous", JSONT, nullable=False),
        sa.Column("new", JSONT, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("history_id"),
        sa.ForeignKeyConstraint(["result_id"], ["test_results.result_id"]),
        sa.UniqueConstraint("correction_ref", "result_id", name="uq_result_history_ref_result"),
        sa.CheckConstraint("kind IN ('MARKS_ADJUSTMENT','REEVALUATION')", name="result_history_kind_check"),
    )
    op.create_index("ix_result_history_result_id", "result_history", ["result_id"])
    op.create_index("ix_result_history_test_id", "result_history", ["test_id"])
    if op.get_bind().dialect.name == "postgresql":
        # History is append-only for the application role: no edits, no deletes.
        op.execute("""
            CREATE FUNCTION result_history_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'result_history is append-only'; END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("CREATE TRIGGER result_history_immutable BEFORE UPDATE OR DELETE ON result_history "
                   "FOR EACH ROW EXECUTE FUNCTION result_history_immutable();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS result_history_immutable ON result_history")
        op.execute("DROP FUNCTION IF EXISTS result_history_immutable()")
    op.drop_index("ix_result_history_test_id", table_name="result_history")
    op.drop_index("ix_result_history_result_id", table_name="result_history")
    op.drop_table("result_history")
