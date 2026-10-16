"""Apply the institute Alembic chain to a scratch SQLite DB (never a real database) and
confirm it matches the ORM models. Tables owned by other services are stubbed first."""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.database import Base

ROOT = Path(__file__).resolve().parents[1]


def _cfg(conn):
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["connection"] = conn
    return cfg


def _engine_with_external_stubs():
    engine = sa.create_engine("sqlite://", poolclass=sa.pool.StaticPool, connect_args={"check_same_thread": False})
    meta = sa.MetaData()
    sa.Table("providers", meta, sa.Column("provider_id", sa.Uuid, primary_key=True))
    sa.Table("branches", meta, sa.Column("branch_id", sa.Uuid, primary_key=True))
    sa.Table("batches", meta, sa.Column("batch_id", sa.Uuid, primary_key=True))
    meta.create_all(engine)
    return engine


def test_single_head_and_linear_history():
    script = ScriptDirectory.from_config(_cfg(None))
    assert len(script.get_heads()) == 1


def test_upgrade_matches_models_and_downgrades():
    engine = _engine_with_external_stubs()
    with engine.connect() as conn:
        command.upgrade(_cfg(conn), "head")
        conn.commit()
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn, opts={"include_object": lambda o, n, t, r, c: not (t == "table" and n in {
            "students", "providers", "branches", "batches", "enrollments", "enrollment_batches", "m10_alembic_version"})})
        diff = [d for d in compare_metadata(mc, Base.metadata)
                if not (d[0] in {"add_table"} and d[1].name in {"students", "enrollments", "enrollment_batches"})]
        # Ignore pure server-default / type-affinity noise from SQLite; structural diffs must be empty.
        structural = [d for d in diff if d[0] in {"add_table", "remove_table", "add_column", "remove_column", "add_index", "remove_index"}]
        assert structural == [], structural
    with engine.connect() as conn:
        command.downgrade(_cfg(conn), "base")
        conn.commit()
    assert "staff" not in sa.inspect(engine).get_table_names()
    assert "providers" in sa.inspect(engine).get_table_names()   # external tables untouched
