import re

from sqlalchemy import select
from sqlalchemy.orm import Session


def next_code(db: Session, column, prefix: str, width: int = 6) -> str:
    """Next human-readable code (STF000001). The UNIQUE constraint is the real guard;
    callers retry on IntegrityError."""
    last = db.scalar(select(column).where(column.like(f"{prefix}%")).order_by(column.desc()).limit(1))
    n = int(re.sub(r"\D", "", last) or 0) + 1 if last else 1
    return f"{prefix}{n:0{width}d}"
