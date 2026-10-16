"""Every test gets its own in-memory Redis: no test may reach a real Redis (sessions, rate limits, idempotency keys)."""
import fakeredis
import pytest


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    from app import auth as auth_module
    from app.routes import auth as auth_routes
    from app.routes import internal as internal_routes

    fake = fakeredis.FakeRedis(decode_responses=True)
    for module in (auth_module, auth_routes, internal_routes):
        monkeypatch.setattr(module, "redis_client", fake)
    yield fake
