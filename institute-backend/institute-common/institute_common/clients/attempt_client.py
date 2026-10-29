"""Client for the Student attempt service: attempt status per exam, and the exam-integrity record and review decision of an attempt."""
from typing import Protocol

from institute_common.config import settings
from institute_common.internal_http import InternalHttp, InternalServiceError


class AttemptClient(Protocol):
    def exam_attempts(self, test_id: str) -> list[dict]: ...
    def attempt_integrity(self, attempt_id: str) -> dict | None: ...
    def test_reviews(self, test_id: str) -> list[dict]: ...
    def review_decision(self, attempt_id: str, body: dict) -> tuple[int, dict]: ...


class HttpAttemptClient:
    def __init__(self) -> None:
        self._h = InternalHttp(settings.student_attempt_service_url, settings.student_attempt_service_api_key)

    def exam_attempts(self, test_id: str) -> list[dict]:
        r = self._h.request("GET", f"/tests/{test_id}/attempts")
        if r.status_code != 200:
            raise InternalServiceError(f"attempt service refused the request ({r.status_code})")
        return r.json()

    def attempt_integrity(self, attempt_id: str) -> dict | None:
        r = self._h.request("GET", f"/attempts/{attempt_id}/integrity")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise InternalServiceError(f"attempt service refused the request ({r.status_code})")
        return r.json()

    def test_reviews(self, test_id: str) -> list[dict]:
        r = self._h.request("GET", f"/tests/{test_id}/reviews")
        if r.status_code != 200:
            raise InternalServiceError(f"attempt service refused the request ({r.status_code})")
        return r.json()

    def review_decision(self, attempt_id: str, body: dict) -> tuple[int, dict]:
        r = self._h.request("POST", f"/attempts/{attempt_id}/review-decision", json=body)
        if r.status_code >= 500:
            raise InternalServiceError(f"attempt service failed ({r.status_code})")
        return r.status_code, r.json()


class UnconfiguredAttemptClient:
    def exam_attempts(self, test_id):
        raise InternalServiceError("Attempt service is not configured.")

    def attempt_integrity(self, attempt_id):
        raise InternalServiceError("Attempt service is not configured.")

    def test_reviews(self, test_id):
        raise InternalServiceError("Attempt service is not configured.")

    def review_decision(self, attempt_id, body):
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
