import time
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, create_engine, event, exc
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import JSON, TypeDecorator

from institute_common.config import settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """TIMESTAMPTZ that always round-trips as an aware UTC datetime (also on SQLite)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


IPAddress = INET().with_variant(String(45), "sqlite")
JSONType = JSONB().with_variant(JSON(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


engine = create_engine(settings.database_url)


@event.listens_for(engine, "checkin")
def _checked_in(_dbapi_connection, record):
    record.info["idle_since"] = time.monotonic()


@event.listens_for(engine, "checkout")
def _ping_if_idle(dbapi_connection, record, _proxy):
    """pool_pre_ping costs a database round trip on EVERY checkout (~200 ms to the hosted database, twice per API request: the service
    and the identity check). Only a connection that sat idle longer than db_ping_after_idle_seconds is pinged; a dead one is discarded
    and the pool reconnects (DisconnectionError = SQLAlchemy's pessimistic-disconnect protocol), exactly as pre-ping would."""
    idle_since = record.info.get("idle_since")
    if idle_since is None or time.monotonic() - idle_since < settings.db_ping_after_idle_seconds:
        return
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT 1")
        finally:
            cursor.close()
    except Exception as e:  # noqa: BLE001 - any failure means the connection cannot be trusted
        raise exc.DisconnectionError() from e
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def new_session() -> Session:
    """A session bound to the current service's ownership rules (writes to foreign tables raise)."""
    from institute_common.ownership import attach_guard
    db = SessionLocal()
    attach_guard(db)
    return db


def get_db():
    db = new_session()
    try:
        yield db
    finally:
        db.close()
