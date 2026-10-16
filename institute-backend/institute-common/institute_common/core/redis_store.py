import redis

from institute_common.config import settings

_client = None


def client() -> redis.Redis:
    """Created on first use: only the identity service has a Redis URL."""
    global _client
    if _client is None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is not configured for this service")
        _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def set_client(new_client) -> None:
    """Test hook: swap the Redis client (e.g. fakeredis)."""
    global _client
    _client = new_client
