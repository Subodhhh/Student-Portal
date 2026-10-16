import json

from fastapi import Cookie, HTTPException
from redis import Redis

from app.config import settings


def get_current_student(
    session_id: str | None = Cookie(default=None),
):
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )

    session_data = redis.get(
        f"student_session:{session_id}"
    )

    if not session_data:
        raise HTTPException(
            status_code=401,
            detail="Session expired or invalid",
        )

    session = json.loads(session_data)

    student_id = session.get("student_id")

    if not student_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid session",
        )

    return student_id