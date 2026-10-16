import json
from datetime import datetime, timezone

import redis
from fastapi import Cookie, HTTPException

from app.config import settings


redis_client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
)


def get_current_student_id(
    session_id: str | None = Cookie(default=None),
) -> str:
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    session_data = redis_client.get(
        f"student_session:{session_id}"
    )

    if not session_data:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    session = json.loads(session_data)

    expires_at = datetime.fromisoformat(
        session["expires_at"]
    )

    if expires_at <= datetime.now(timezone.utc):
        redis_client.delete(
            f"student_session:{session_id}"
        )

        raise HTTPException(
            status_code=401,
            detail="Session expired",
        )

    return session["student_id"]