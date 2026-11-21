"""Test-configured marking rules (additive)

  * exam_plans, test_series, global_tests gain `marking_rules` (JSONB, nullable): the test creator's marks per question type and
    subject for the new marking mode RULES (decimals stored as exact strings, see institute_common.core.marking).
  * Their `<table>_marking_check` now also allows 'RULES', and a new `<table>_marking_rules_check` keeps the two in step:
    RULES always has rules, PER_QUESTION / UNIFORM never do.

Existing rows are untouched: they are PER_QUESTION or UNIFORM with NULL rules, which both checks accept. Adding a nullable column
without a default does not rewrite the table in PostgreSQL. A database created after these joined the models already has them
(0001 baseline creates the current models); the checks are then simply recreated with the same definition.

PostgreSQL only (DROP CONSTRAINT IF EXISTS), like every revision of this chain that changes constraints.

Revision ID: 0013_marking_rules
Revises: 0012_exam_branch_indexes
"""
import sqlalchemy as sa
from alembic import op

from institute_common.database import JSONType
from institute_common.migrations import existing_columns

revision = "0013_marking_rules"
down_revision = "0012_exam_branch_indexes"
branch_labels = None
depends_on = None

TABLES = ("exam_plans", "test_series", "global_tests")


def _replace_check(table: str, name: str, condition: str) -> None:
    """Drop the named CHECK when present and create it with `condition` (table and names are constants of this file)."""
    op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{name}"')
    op.execute(f'ALTER TABLE {table} ADD CONSTRAINT "{name}" CHECK ({condition})')


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        if "marking_rules" not in existing_columns(bind, table):
            op.add_column(table, sa.Column("marking_rules", JSONType, nullable=True))
        _replace_check(table, f"{table}_marking_check", "marking_mode IN ('PER_QUESTION','UNIFORM','RULES')")
        _replace_check(table, f"{table}_marking_rules_check", "(marking_mode = 'RULES') = (marking_rules IS NOT NULL)")


def downgrade() -> None:
    bind = op.get_bind()
    # Refuse rather than silently change the marks of a test: RULES tests have no representation without this revision.
    for table in TABLES:
        in_use = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE marking_mode = 'RULES'")).scalar()
        if in_use:
            raise RuntimeError(f"{table} has {in_use} row(s) using RULES marking; change their marking before downgrading.")
    for table in TABLES:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{table}_marking_rules_check"')
        _replace_check(table, f"{table}_marking_check", "marking_mode IN ('PER_QUESTION','UNIFORM')")
        if "marking_rules" in existing_columns(bind, table):
            op.drop_column(table, "marking_rules")
