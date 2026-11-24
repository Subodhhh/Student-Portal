import time

from sqlalchemy import create_engine, event, exc
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


# Base class used by our database models.
class Base(DeclarativeBase):
    pass


# Connect SQLAlchemy to PostgreSQL/Neon.
engine = create_engine(settings.database_url, **({} if settings.database_url.startswith("sqlite") else dict(
    pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow, pool_timeout=settings.db_pool_timeout)))


# Ping a pooled connection only when it sat idle (a ping on EVERY checkout costs one database round trip per request, ~200 ms to the
# hosted database). A dead connection fails the ping and is replaced (DisconnectionError = SQLAlchemy's pessimistic-disconnect
# protocol), exactly as pool_pre_ping would; the same rule as the Institute services (institute_common/database.py).
PING_AFTER_IDLE_SECONDS = 30


@event.listens_for(engine, "checkin")
def _checked_in(_dbapi_connection, record):
    record.info["idle_since"] = time.monotonic()


@event.listens_for(engine, "checkout")
def _ping_if_idle(dbapi_connection, record, _proxy):
    idle_since = record.info.get("idle_since")
    if idle_since is None or time.monotonic() - idle_since < PING_AFTER_IDLE_SECONDS:
        return
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT 1")
        finally:
            cursor.close()
        dbapi_connection.rollback()          # end the ping's transaction: the connection is handed out idle
    except Exception as e:  # noqa: BLE001 - any failure means the connection cannot be trusted
        raise exc.DisconnectionError() from e


# Creates a database session for each API request.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
)
