"""Shared plumbing for calls to a Student service's /internal/v1 API: scoped service key, timeouts,
bounded retries with exponential backoff + jitter (transport errors and 5xx only)."""
import random
import time

import httpx


class InternalServiceError(Exception):
    """The Student service could not be reached or answered 5xx after the retries."""


class InternalRejected(Exception):
    """The Student service refused the request (4xx): retrying the same request cannot help."""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"rejected ({status}) {detail}".strip())
        self.status = status


class InternalHttp:
    RETRIES = 3

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 8.0) -> None:
        if not base_url or not api_key:
            raise InternalServiceError("service is not configured")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/internal/v1",
            headers={"X-Service-Key": api_key, "X-Service-Name": "institute-backend"},
            timeout=httpx.Timeout(timeout, connect=2.0),
        )

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.RETRIES):
            try:
                r = self._http.request(method, path, **kw)
                if r.status_code < 500:
                    return r
                last = InternalServiceError(f"service returned {r.status_code}")
            except httpx.TransportError as exc:
                last = exc
            time.sleep(min(2.0, 0.2 * 2**attempt) * (0.5 + random.random()))
        raise InternalServiceError("service unavailable") from last
