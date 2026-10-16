from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  (register all tables)
from app.config import settings
from app.database import Base

config = context.config
# Migrations use a separate, more privileged role than the runtime service.
_url = __import__("os").environ.get("MIGRATION_DATABASE_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Own version table: never collides with the student services (default, m3_..m9_).
VERSION_TABLE = "m10_alembic_version"

# Tables owned by OTHER services / the enrollment chain. They are mapped for
# querying but must never be created, altered or dropped by this chain.
EXTERNAL_TABLES = {"students", "providers", "branches", "batches", "enrollments", "enrollment_batches"}


def include_object(obj, name, type_, reflected, compare_to):
    """Autogenerate must only ever look at tables THIS service owns. The database is shared:
    other services' tables (and their *_alembic_version tables) exist in it, and without this filter
    `alembic revision --autogenerate` would propose dropping them."""
    if type_ == "table":
        if name in EXTERNAL_TABLES:
            return False
        if reflected and compare_to is None:  # present in the DB but not in our models => not ours
            return False
        return True
    owner = getattr(getattr(obj, "table", None), "name", None)
    if owner is not None and (owner in EXTERNAL_TABLES or owner not in target_metadata.tables):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"}, version_table=VERSION_TABLE, include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata,
        version_table=VERSION_TABLE, include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided = config.attributes.get("connection")  # used by tests
    if provided is not None:
        _run(provided)
        return
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
