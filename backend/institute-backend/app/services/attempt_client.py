"""Read-only client for the Student attempt service (attempt status per exam)."""
from typing import Protocol

from app.config import settings
from app.services.internal_http import InternalHttp, InternalServiceError


class AttemptClient(Protocol):
    def exam_attempts(self, test_id: str) -> list[dict]: ...


class HttpAttemptClient:
    def __init__(self) -> None:
        self._h = InternalHttp(settings.student_attempt_service_url, settings.student_attempt_service_api_key)

    def exam_attempts(self, test_id: str) -> list[dict]:
        r = self._h.request("GET", f"/tests/{test_id}/attempts")
        if r.status_code != 200:
            raise InternalServiceError(f"attempt service refused the request ({r.status_code})")
        return r.json()


class UnconfiguredAttemptClient:
    def exam_attempts(self, test_id):
        raise InternalServiceError("Attempt service is not configured.")


_client: AttemptClient | None = None


def get_attempt_client() -> AttemptClient:
    global _client
    if _client is None:
        try:
            _client = HttpAttemptClient()
        except InternalServiceError:
            return UnconfiguredAttemptClient()
    return _client


def set_attempt_client(c: AttemptClient | None) -> None:
    global _client
    _client = c
