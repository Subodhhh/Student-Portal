from app.core import redis_store


def hit(purpose: str, kind: str, key: str, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter. Returns True while the caller is within the limit."""
    k = f"inst_rl:{purpose}:{kind}:{key}"
    r = redis_store.client()
    count = r.incr(k)
    if count == 1:
        r.expire(k, window_seconds)
    return count <= limit


def reset(purpose: str, kind: str, key: str) -> None:
    redis_store.client().delete(f"inst_rl:{purpose}:{kind}:{key}")
