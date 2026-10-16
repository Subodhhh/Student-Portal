"""Shared plumbing for calls to a Student service's /internal/v1 API: scoped service key, timeouts,
bounded retries with exponential backoff + jitter (transport errors and 5xx only)."""
import hmac
import random
import time

import httpx
from fastapi import Header, HTTPException

from institute_common.config import settings


class InternalServiceError(Exception):
    """The Student service could not be reached or answered 5xx after the retries."""


class InternalRejected(Exception):
    """The Student service refused the request (4xx): retrying the same request cannot help."""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"rejected ({status}) {detail}".strip())
        self.status = status


# Test hook: in-process peers (service name -> httpx.Client such as a Starlette TestClient). Never set in production.
_PEERS: dict[str, httpx.Client] = {}


def set_peer_client(service: str, client: httpx.Client | None) -> None:
    if client is None:
        _PEERS.pop(service, None)
    else:
        _PEERS[service] = client


def require_internal_key(x_service_key: str | None = Header(default=None)) -> None:
    """Dependency for every /internal/v1 router: 404 unless INTERNAL_API_KEYS is configured (the API does not exist), 401 on a bad key."""
    keys = [k.strip() for k in settings.internal_api_keys.split(",") if k.strip()]
    if not keys:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_service_key or not any(hmac.compare_digest(x_service_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


class InternalHttp:
    RETRIES = 3

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 8.0, client: httpx.Client | None = None) -> None:
        if client is not None:
            self._http = client
            self._http.headers.update({"X-Service-Key": api_key or "", "X-Service-Name": settings.service_name})
            self._prefix = "/internal/v1"
            return
        if not base_url or not api_key:
            raise InternalServiceError("service is not configured")
        self._prefix = ""
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/internal/v1",
            headers={"X-Service-Key": api_key, "X-Service-Name": settings.service_name},
            timeout=httpx.Timeout(timeout, connect=2.0),
        )

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.RETRIES):
            try:
                r = self._http.request(method, self._prefix + path, **kw)
                if r.status_code < 500:
                    return r
                last = InternalServiceError(f"service returned {r.status_code}")
            except httpx.TransportError as exc:
                last = exc
            time.sleep(min(2.0, 0.2 * 2**attempt) * (0.5 + random.random()))
        raise InternalServiceError("service unavailable") from last


def peer(service: str, *, timeout: float = 5.0) -> InternalHttp:
    """Client for another Institute service's internal API (URL and key come from this service's environment)."""
    if service in _PEERS:
        key = getattr(settings, f"{service}_service_api_key") or settings.internal_api_keys.split(",")[0].strip()
        return InternalHttp("", key, client=_PEERS[service])
    return InternalHttp(getattr(settings, f"{service}_service_url"), getattr(settings, f"{service}_service_api_key"), timeout=timeout)


def readiness(service: str, *, timeout: float = 2.0) -> dict:
    """Public /ready probe of another Institute service (no key needed; it reveals only database/redis reachability)."""
    try:
        if service in _PEERS:
            r = _PEERS[service].get("/ready")
        else:
            url = getattr(settings, f"{service}_service_url")
            if not url:
                return {"status": "not_configured"}
            r = httpx.get(url.rstrip("/") + "/ready", timeout=timeout)
        body = r.json()
        return {"status": "ok" if r.status_code == 200 else "degraded", **{k: v for k, v in body.items() if k in {"database", "redis"}}}
    except Exception:  # noqa: BLE001 - an unreachable service is exactly what this probe reports
        return {"status": "unreachable"}
