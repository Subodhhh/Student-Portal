"""Question types MSQ and NAT (expand only; docs/institute-portal/QUESTION_TYPES_ARCHITECTURE.md)

  * questions.content / answer_key (JSONB) and scheme (VARCHAR 30): an MSQ's options and correct set, a NAT's number format and
    value + tolerance. `content` is served to students; `answer_key` and `scheme` never are before review. NULL for MCQ, whose
    options and key stay in the existing columns.
  * question_pictures: pictures referenced inside `content` (MSQ option pictures), so the picture endpoint authorises them.

Existing rows are unchanged (MCQ, question_type 'MCQ'); old code runs unchanged on the upgraded schema. Downgrade refuses while
an MSQ / NAT question exists.

Revision ID: g1_question_types
Revises: e4b9a6c1d8f3
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "g1_question_types"
down_revision = "e4b9a6c1d8f3"
branch_labels = None
depends_on = None


def upgrade():
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("questions")}
    for name, column in (("content", sa.Column("content", JSONB(), nullable=True)),
                         ("answer_key", sa.Column("answer_key", JSONB(), nullable=True)),
                         ("scheme", sa.Column("scheme", sa.String(30), nullable=True))):
        if name not in have:
            op.add_column("questions", column)
    if "question_pictures" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("question_pictures",
                        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.question_id", ondelete="CASCADE"), primary_key=True),
                        sa.Column("role", sa.String(20), primary_key=True),
                        sa.Column("checksum", sa.String(64), sa.ForeignKey("question_images.checksum"), nullable=False))
        op.create_index("ix_question_pictures_checksum", "question_pictures", ["checksum"])


def downgrade():
    bind = op.get_bind()
    typed = bind.execute(sa.text("SELECT count(*) FROM questions WHERE question_type IN ('MCQ_MULTI', 'NUMERICAL')")).scalar()
    if typed:
        raise RuntimeError(f"{typed} MSQ/NAT question(s) exist; they cannot be represented before this revision. Downgrade refused.")
    op.drop_table("question_pictures")
    for name in ("scheme", "answer_key", "content"):
        op.drop_column("questions", name)
