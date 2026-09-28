import json
import secrets
from datetime import datetime, timedelta, timezone

import redis

from app.config import settings

SESSION_DURATION = timedelta(minutes=30)

redis_client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
)


def create_session(student_id: str) -> tuple[str, datetime]:
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + SESSION_DURATION

    session_data = json.dumps(
        {
            "student_id": student_id,
            "expires_at": expires_at.isoformat(),
        }
    )

    redis_client.set(
        f"student_session:{session_id}",
        session_data,
        ex=int(SESSION_DURATION.total_seconds()),
    )

    redis_client.sadd(
        f"student_sessions:{student_id}",
        session_id,
    )

    redis_client.expire(
        f"student_sessions:{student_id}",
        int(SESSION_DURATION.total_seconds()),
    )

    return session_id, expires_at


def get_session(session_id: str) -> dict | None:
    session_data = redis_client.get(f"student_session:{session_id}")

    if not session_data:
        return None

    return json.loads(session_data)


def delete_session(session_id: str) -> None:
    session_data = redis_client.get(f"student_session:{session_id}")

    if session_data:
        data = json.loads(session_data)
        student_id = data["student_id"]

        redis_client.srem(
            f"student_sessions:{student_id}",
            session_id,
        )

    redis_client.delete(f"student_session:{session_id}")


def delete_student_sessions(student_id: str) -> None:
    session_ids = redis_client.smembers(
        f"student_sessions:{student_id}"
    )

    if session_ids:
        redis_client.delete(
            *[f"student_session:{session_id}" for session_id in session_ids]
        )

    redis_client.delete(
        f"student_sessions:{student_id}"
    )