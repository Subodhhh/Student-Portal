"""Question types: MCQ, MSQ and NAT (expand only; docs/institute-portal/QUESTION_TYPES_ARCHITECTURE.md)

  * question_versions.question_type (VARCHAR 20, NOT NULL, default 'MCQ_SINGLE'): the type copied onto every immutable version
    (decision D2). Every existing version is an MCQ and gets 'MCQ_SINGLE' from the default (no row is rewritten or updated, so the
    append-only trigger is not involved).
  * question_versions.content / answer_key (JSONB) and schema_version: the MSQ options or NAT number format, and their key.
  * question_versions.correct_option becomes nullable (MSQ and NAT have no correct letter). question_versions_correct_check is
    replaced by question_versions_shape_check, which ties the shape to the type: an MCQ exactly as before; an MSQ / NAT with
    content + key, empty option columns without pictures and no letter. question_versions_msq_negative_check: an MSQ's own marks
    have no negative part (all-or-nothing, never negative).
  * bank_questions_type_check / question_versions_type_check: MCQ_SINGLE, MCQ_MULTI, NUMERICAL.
  * question_version_assets: pictures referenced inside content (MSQ option pictures), append-only like the versions.

Existing rows keep every value, ID and constraint they satisfied; the old code runs unchanged on the upgraded schema. Downgrade refuses
while any MSQ / NAT question, version or picture link exists. Idempotent for a database whose baseline already has these shapes.

Revision ID: 0015_question_types
Revises: 0014_three_scope_question_bank
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType
from institute_common.migrations import FUNCTIONS, existing_columns

revision = "0015_question_types"
down_revision = "0014_three_scope_question_bank"
branch_labels = None
depends_on = None

TYPES = "'MCQ_SINGLE','MCQ_MULTI','NUMERICAL'"
SHAPE = ("(question_type = 'MCQ_SINGLE' AND correct_option IS NOT NULL AND correct_option IN ('A','B','C','D') AND content IS NULL AND answer_key IS NULL) OR "
         "(question_type IN ('MCQ_MULTI','NUMERICAL') AND correct_option IS NULL AND content IS NOT NULL AND "
         "answer_key IS NOT NULL AND schema_version IS NOT NULL AND option_a = '' AND option_b = '' AND option_c = '' AND "
         "option_d = '' AND option_a_image_id IS NULL AND option_b_image_id IS NULL AND option_c_image_id IS NULL AND "
         "option_d_image_id IS NULL)")
MSQ_NEGATIVE = "question_type <> 'MCQ_MULTI' OR marks_negative IS NULL OR marks_negative = 0"


def upgrade() -> None:
    bind = op.get_bind()
    have = existing_columns(bind, "question_versions")
    if "question_type" not in have:
        op.add_column("question_versions", sa.Column("question_type", sa.String(20), nullable=False, server_default="MCQ_SINGLE"))
    for name, column in (("content", sa.Column("content", JSONType, nullable=True)),
                         ("answer_key", sa.Column("answer_key", JSONType, nullable=True)),
                         ("schema_version", sa.Column("schema_version", sa.Integer(), nullable=True))):
        if name not in have:
            op.add_column("question_versions", column)
    op.execute("ALTER TABLE question_versions ALTER COLUMN correct_option DROP NOT NULL")
    for name, rule in (("question_versions_correct_check", None), ("question_versions_shape_check", SHAPE),
                       ("question_versions_msq_negative_check", MSQ_NEGATIVE),
                       ("question_versions_type_check", f"question_type IN ({TYPES})")):
        op.execute(f'ALTER TABLE question_versions DROP CONSTRAINT IF EXISTS "{name}"')
        if rule:
            op.execute(f'ALTER TABLE question_versions ADD CONSTRAINT "{name}" CHECK ({rule})')
    op.execute('ALTER TABLE bank_questions DROP CONSTRAINT IF EXISTS "bank_questions_type_check"')
    op.execute(f'ALTER TABLE bank_questions ADD CONSTRAINT "bank_questions_type_check" CHECK (question_type IN ({TYPES}))')

    if "question_version_assets" not in sa.inspect(bind).get_table_names():
        op.create_table("question_version_assets",
                        sa.Column("question_version_id", sa.Uuid(), sa.ForeignKey("question_versions.question_version_id"), primary_key=True),
                        sa.Column("role", sa.String(20), primary_key=True),
                        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("question_image_assets.asset_id"), nullable=False))
        op.create_index("ix_question_version_assets_asset_id", "question_version_assets", ["asset_id"])
    op.execute(FUNCTIONS["institute_history_immutable"])
    op.execute("DROP TRIGGER IF EXISTS question_version_assets_immutable ON question_version_assets")
    op.execute("CREATE TRIGGER question_version_assets_immutable BEFORE UPDATE OR DELETE ON question_version_assets "
               "FOR EACH ROW EXECUTE FUNCTION institute_history_immutable()")


def downgrade() -> None:
    bind = op.get_bind()
    count = lambda sql: bind.execute(sa.text(sql)).scalar()      # noqa: E731
    typed = count("SELECT count(*) FROM question_versions WHERE question_type <> 'MCQ_SINGLE'")
    questions = count("SELECT count(*) FROM bank_questions WHERE question_type <> 'MCQ_SINGLE'")
    links = count("SELECT count(*) FROM question_version_assets")
    if typed or questions or links:
        raise RuntimeError(f"{questions} MSQ/NAT question(s), {typed} MSQ/NAT version(s) and {links} option picture link(s) exist; "
                           "they cannot be represented before this revision. Downgrade refused.")
    op.execute("DROP TRIGGER IF EXISTS question_version_assets_immutable ON question_version_assets")
    op.drop_table("question_version_assets")
    op.execute('ALTER TABLE bank_questions DROP CONSTRAINT IF EXISTS "bank_questions_type_check"')
    op.execute("ALTER TABLE bank_questions ADD CONSTRAINT \"bank_questions_type_check\" CHECK (question_type IN ('MCQ_SINGLE'))")
    for name in ("question_versions_type_check", "question_versions_msq_negative_check", "question_versions_shape_check"):
        op.execute(f'ALTER TABLE question_versions DROP CONSTRAINT IF EXISTS "{name}"')
    op.execute("ALTER TABLE question_versions ADD CONSTRAINT \"question_versions_correct_check\" CHECK (correct_option IN ('A','B','C','D'))")
    op.execute("ALTER TABLE question_versions ALTER COLUMN correct_option SET NOT NULL")
    for name in ("schema_version", "answer_key", "content", "question_type"):
        op.drop_column("question_versions", name)
