"""students.date_of_birth: the optional date of birth the Student profile service reads and updates (additive)

The profile service maps students.date_of_birth (DATE, nullable) and lets a student set or clear it, but no migration created the
column: a database built from these migrations made GET /profile fail (UndefinedColumn -> 500). The authentication service owns the
students table, so the column is added here. It is optional: existing rows stay NULL and nothing requires a value.

Older databases may already hold the column (the legacy schema had it). It is then adopted only if it is exactly what this migration
creates - DATE, nullable, no default - and any other shape stops the upgrade instead of being silently accepted.

Revision ID: 0003_students_date_of_birth
Revises: 0002_student_id_allocator
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_students_date_of_birth"
down_revision = "0002_student_id_allocator"
branch_labels = None
depends_on = None


def _public_search_path() -> None:
    # Transaction-local: a pooled connection may carry an empty session search_path (see alembic/env.py and 0002).
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL search_path TO public")


def upgrade() -> None:
    _public_search_path()
    bind = op.get_bind()
    existing = {c["name"]: c for c in sa.inspect(bind).get_columns("students")}
    if "date_of_birth" not in existing:
        op.add_column("students", sa.Column("date_of_birth", sa.Date(), nullable=True))
        return
    have = existing["date_of_birth"]
    have_type = have["type"].compile(dialect=bind.dialect)
    problems = []
    if have_type.upper() != "DATE":
        problems.append(f"type {have_type} (expected DATE)")
    if not have["nullable"]:
        problems.append("NOT NULL (expected nullable)")
    if have.get("default") is not None:
        problems.append(f"default {have['default']} (expected none: a default would invent a date of birth)")
    if problems:
        raise RuntimeError("students.date_of_birth already exists but differs from what 0003_students_date_of_birth creates: "
                           + "; ".join(problems) + ". Fix the column by hand before upgrading.")


def downgrade() -> None:
    _public_search_path()
    op.drop_column("students", "date_of_birth")
