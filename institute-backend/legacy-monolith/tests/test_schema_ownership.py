"""Guards the shared-database boundary: this service must never create a table the Student services own,
and must never alter or drop anything it does not own."""
import re
from pathlib import Path

import pytest

from app import models  # noqa: F401
from app.database import Base

# <repo>/institute-backend/legacy-monolith/tests -> <repo>/student-backend
BACKEND = Path(__file__).resolve().parents[3] / "student-backend"
# Mapped for querying only; created by other services' chains.
EXTERNAL = {"students", "providers", "branches", "batches", "enrollments", "enrollment_batches"}


def student_owned_tables() -> set[str]:
    names: set[str] = set()
    for f in list(BACKEND.glob("student-*/app/**/*.py")) + list(BACKEND.glob("student-*/alembic/versions/*.py")):
        text = f.read_text()
        names |= set(re.findall(r'__tablename__ = "([a-z_]+)"', text))
        names |= set(re.findall(r"create_table\(\s*['\"]([a-z_]+)['\"]", text))
    return names


@pytest.mark.skipif(not list(BACKEND.glob("student-*")), reason="student services not present")
def test_no_institute_table_collides_with_a_student_owned_table():
    ours = {t for t in Base.metadata.tables if t not in EXTERNAL}
    clash = ours & student_owned_tables()
    assert clash == set(), f"Institute tables that collide with Student-owned tables: {sorted(clash)}"


def test_migrations_only_create_tables_this_service_owns():
    created = set()
    for f in (Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*.py"):
        created |= set(re.findall(r"create_table\(\s*['\"]([a-z_]+)['\"]", f.read_text()))
    assert created == {t for t in Base.metadata.tables if t not in EXTERNAL}
    assert not (created & EXTERNAL)


def test_migrations_never_alter_or_drop_foreign_tables():
    for f in (Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*.py"):
        text = f.read_text()
        for ext in EXTERNAL | student_owned_tables() - {"questions"}:
            # only FKs *to* shared tables are allowed (sa.ForeignKey("providers.provider_id")), never DDL on them
            assert not re.search(rf"op\.(add_column|drop_column|alter_column|drop_table|create_index|drop_index|create_foreign_key|drop_constraint)\(\s*['\"]{ext}['\"]", text), (f.name, ext)
