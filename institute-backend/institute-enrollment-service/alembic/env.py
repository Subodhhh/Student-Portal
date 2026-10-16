import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import institute_common.models  # noqa: F401  (register every mapped table)
from institute_common.config import settings
from institute_common.database import Base
from institute_common.ownership import STUDENT_CHAIN_TABLES, owned_tables

SERVICE = "enrollment"
config = context.config
# Migrations use a separate, more privileged role than the runtime service.
_url = os.environ.get("MIGRATION_DATABASE_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
VERSION_TABLE = f"inst_{SERVICE}_alembic_version"       # own history: never collides with the legacy m10_ chain or any Student chain
OWNED = owned_tables(SERVICE) - STUDENT_CHAIN_TABLES   # tables whose DDL this service manages


def include_object(obj, name, type_, reflected, compare_to):
    """Autogenerate/check look only at tables this service owns; the database is shared and holds everyone else's tables."""
    if type_ == "table":
        return name in OWNED
    owner = getattr(getattr(obj, "table", None), "name", None)
    return owner in OWNED if owner is not None else True


def run_migrations_offline() -> None:
    context.configure(url=_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"},
                      version_table=VERSION_TABLE, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, version_table=VERSION_TABLE, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided = config.attributes.get("connection")
    if provided is not None:
        _run(provided)
        return
    engine = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
