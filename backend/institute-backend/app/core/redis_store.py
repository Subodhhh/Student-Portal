import redis

from app.config import settings

_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def client() -> redis.Redis:
    return _client


def set_client(new_client) -> None:
    """Test hook: swap the Redis client (e.g. fakeredis)."""
    global _client
    _client = new_client
