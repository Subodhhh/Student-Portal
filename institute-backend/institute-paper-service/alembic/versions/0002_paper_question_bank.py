"""question bank: global (platform) questions and papers, question taxonomy, separate answer-key uploads (additive)

  * bank_questions: `scope` (PROVIDER | GLOBAL, existing rows PROVIDER), provider_id/branch_id nullable for GLOBAL rows only
    (enforced by a check), classification columns (exam type, class, chapter, topic, subtopic, difficulty, pattern, subtype,
    question type) and `forked_from_question_id` (a provider's own copy of a question it corrected but does not own). `topic` is backfilled from each question's latest version; versions themselves are untouched (immutable).
  * papers: the same `scope` rule plus optional exam type / class.
  * paper_uploads: optional answer-key file and the upload title.
Nothing is dropped or rewritten; every new column is nullable or has a server default.

Revision ID: 0002_paper_question_bank
Revises: 0001_paper_baseline
"""
import sqlalchemy as sa
from alembic import op

from institute_common.core.taxonomy import DIFFICULTIES, PATTERNS, QUESTION_TYPES, sql_in
from institute_common.migrations import existing_checks, existing_columns, existing_indexes, nullable

revision = "0002_paper_question_bank"
down_revision = "0001_paper_baseline"
branch_labels = None
depends_on = None

SCOPE_RULE = ("(scope = 'GLOBAL' AND provider_id IS NULL AND branch_id IS NULL) OR "
              "(scope = 'PROVIDER' AND provider_id IS NOT NULL AND branch_id IS NOT NULL)")


def _add(bind, table: str, *columns: sa.Column) -> None:
    have = existing_columns(bind, table)
    for col in columns:
        if col.name not in have:
            op.add_column(table, col)


def _check(bind, table: str, name: str, rule: str) -> None:
    if name not in existing_checks(bind, table):
        op.create_check_constraint(name, table, rule)


def _relax(bind, table: str, column: str) -> None:
    if not nullable(bind, table, column):
        op.alter_column(table, column, existing_type=sa.Uuid(), nullable=True)


def upgrade() -> None:
    bind = op.get_bind()
    _add(bind, "bank_questions",
         sa.Column("scope", sa.String(10), nullable=False, server_default="PROVIDER"),
         sa.Column("exam_type", sa.String(80)), sa.Column("target_class", sa.String(80)), sa.Column("chapter", sa.String(80)),
         sa.Column("topic", sa.String(200)), sa.Column("subtopic", sa.String(80)), sa.Column("difficulty", sa.String(10)),
         sa.Column("pattern", sa.String(30), nullable=False, server_default="SINGLE_CORRECT"),
         sa.Column("subtype", sa.String(80)),
         sa.Column("question_type", sa.String(20), nullable=False, server_default="MCQ_SINGLE"),
         sa.Column("forked_from_question_id", sa.Uuid(), sa.ForeignKey("bank_questions.question_id")),
         sa.Column("updated_at", sa.DateTime(timezone=True)))
    for col in ("provider_id", "branch_id"):
        _relax(bind, "bank_questions", col)
    _check(bind, "bank_questions", "bank_questions_scope_check", SCOPE_RULE)
    _check(bind, "bank_questions", "bank_questions_difficulty_check", f"difficulty IS NULL OR difficulty IN ({sql_in(DIFFICULTIES)})")
    _check(bind, "bank_questions", "bank_questions_pattern_check", f"pattern IN ({sql_in(PATTERNS)})")
    _check(bind, "bank_questions", "bank_questions_type_check", f"question_type IN ({sql_in(QUESTION_TYPES)})")
    idx = existing_indexes(bind, "bank_questions")
    if "ix_bank_questions_catalogue" not in idx:
        op.create_index("ix_bank_questions_catalogue", "bank_questions", ["scope", "subject_id", "status"])
    if "ix_bank_questions_taxonomy" not in idx:
        op.create_index("ix_bank_questions_taxonomy", "bank_questions", ["subject_id", "exam_type", "target_class", "chapter"])
    # Carry the free-text topic of the latest version onto the question (classification now lives there). Only fills NULLs.
    op.execute(sa.text("""
        UPDATE bank_questions SET topic = LEFT(lv.topic, 200)
        FROM (SELECT DISTINCT ON (question_id) question_id, topic FROM question_versions
              ORDER BY question_id, version_no DESC) lv
        WHERE lv.question_id = bank_questions.question_id AND bank_questions.topic IS NULL AND lv.topic IS NOT NULL
    """) if bind.dialect.name == "postgresql" else sa.text("SELECT 1"))

    _add(bind, "papers", sa.Column("scope", sa.String(10), nullable=False, server_default="PROVIDER"),
         sa.Column("exam_type", sa.String(80)), sa.Column("target_class", sa.String(80)))
    for col in ("provider_id", "branch_id"):
        _relax(bind, "papers", col)
    _check(bind, "papers", "papers_scope_check", SCOPE_RULE)

    _add(bind, "paper_uploads", sa.Column("answer_key_file_id", sa.Uuid(), sa.ForeignKey("stored_files.file_id")),
         sa.Column("title", sa.String(255)))


def downgrade() -> None:
    # Forward-only by policy: global questions/papers would lose their meaning.
    pass
