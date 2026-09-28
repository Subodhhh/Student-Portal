from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


# Base class used by our database models.
class Base(DeclarativeBase):
    pass


# Connect SQLAlchemy to PostgreSQL/Neon.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)


# Creates a database session for each API request.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
)
