from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.database import Base

from app.models.test import Test  # noqa: F401
from app.models.question import Question  # noqa: F401

config = context.config

config.set_main_option(
    "sqlalchemy.url",
    settings.database_url,
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


target_metadata = Base.metadata

def include_name(name, type_, parent_names):
    if type_ == "table":
        return name in {"tests", "questions"}

    return True

def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "foreign_key_constraint":
        if name in {
            "fk_tests_provider_id",
            "fk_tests_branch_id",
        }:
            return False

    return True

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="m4_alembic_version",
        include_name=include_name,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="m4_alembic_version",
            include_name=include_name,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()