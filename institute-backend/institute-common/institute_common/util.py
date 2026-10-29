from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from institute_common.core.security import api_error
from institute_common.schemas.common import MAX_PAGE_SIZE


def paginate(db: Session, stmt, page: int, page_size: int):
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    offset = (page - 1) * page_size
    if not stmt._distinct:
        # the page and the total in ONE round trip (window count over the filtered rows); a DISTINCT query would count before
        # de-duplication, and an empty page carries no total, so those use the separate count
        found = db.execute(stmt.add_columns(func.count().over().label("_total")).limit(page_size).offset(offset)).all()
        if found:
            return {"items": [r[0] for r in found], "total": found[0][-1], "page": page, "page_size": page_size}
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.limit(page_size).offset(offset)).all()
    return {"items": rows, "total": total or 0, "page": page, "page_size": page_size}


def need_reason(reason: str | None) -> str:
    if not reason or len(reason.strip()) < 5:
        raise api_error(422, "REASON_REQUIRED", "A reason of at least 5 characters is required.")
    return reason.strip()


def escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Strict(BaseModel):
    """Request bodies reject unknown fields."""
    model_config = ConfigDict(extra="forbid")
