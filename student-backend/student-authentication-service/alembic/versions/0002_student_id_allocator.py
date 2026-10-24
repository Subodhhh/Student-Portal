"""durable Student ID allocator: an issued Student ID is never issued again

Additive: creates `student_id_allocator` (one row, name 'STU') holding the highest number ever issued; no existing table, column or row is
changed. Seeded once with the greater of
  * HISTORICAL_FLOOR = 100: a deliberate SAFETY MARGIN, not proof. The highest Student ID found in retained evidence on 2026-10-24 is
    STU000012 (the audit log's student.create events since 2026-10-14, every backup since 2026-10-17); there is no audit trail before
    2026-10-14, so the counter starts well above anything that could plausibly have been issued. The first new ID is STU000101; and
  * the highest STU number present in ANY student_id column of this database at migration time.
An existing row is never changed (re-running cannot lower the counter). Downgrade drops the counter; it is for disposable databases only:
on a real database the highest-issued value would be lost.

Revision ID: 0002_student_id_allocator
Revises: 562cc1f6c4b7
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "0002_student_id_allocator"
down_revision = "562cc1f6c4b7"
branch_labels = None
depends_on = None

HISTORICAL_FLOOR = 100   # safety margin above the highest evidenced ID (STU000012); see the docstring
_ID = re.compile(r"^STU(\d+)$")


def _public_search_path() -> None:
    # Transaction-local: a pooled connection may carry an empty session search_path (see alembic/env.py).
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL search_path TO public")


def _highest_present(bind) -> int:
    highest = 0
    insp = sa.inspect(bind)
    for table in insp.get_table_names():
        if "student_id" in {c["name"] for c in insp.get_columns(table)}:
            for (sid,) in bind.execute(sa.text(f'SELECT DISTINCT student_id FROM "{table}" WHERE student_id LIKE \'STU%\'')):
                m = _ID.match(str(sid))
                if m:
                    highest = max(highest, int(m.group(1)))
    return highest


def upgrade() -> None:
    _public_search_path()
    bind = op.get_bind()
    if "student_id_allocator" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "student_id_allocator",
            sa.Column("name", sa.String(10), primary_key=True),
            sa.Column("last_value", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.CheckConstraint("last_value >= 0", name="student_id_allocator_nonnegative"),
        )
    if bind.execute(sa.text("SELECT count(*) FROM student_id_allocator WHERE name = 'STU'")).scalar() == 0:
        start = max(HISTORICAL_FLOOR, _highest_present(bind))
        bind.execute(sa.text("INSERT INTO student_id_allocator (name, last_value) VALUES ('STU', :v)"), {"v": start})


def downgrade() -> None:
    _public_search_path()
    op.drop_table("student_id_allocator")
