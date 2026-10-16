"""Client for the Student test service's answer-key correction endpoint (internal API, service key)."""
from typing import Protocol

from app.config import settings
from app.services.internal_http import InternalHttp, InternalRejected, InternalServiceError


class AnswerKeyClient(Protocol):
    def set_answer_key(self, *, test_id: str, changes: list[dict], reason: str, actor: str, correction_ref: str) -> dict: ...


class HttpAnswerKeyClient:
    def __init__(self) -> None:
        self._h = InternalHttp(settings.student_test_service_url, settings.student_test_service_api_key)

    def set_answer_key(self, *, test_id, changes, reason, actor, correction_ref) -> dict:
        r = self._h.request("POST", f"/exams/{test_id}/answer-key",
                            json={"changes": changes, "reason": reason, "actor": actor, "correction_ref": correction_ref})
        if r.status_code >= 400:
            try:
                detail = str(r.json().get("detail", ""))[:300]
            except Exception:
                detail = ""
            raise InternalRejected(r.status_code, detail)
        return r.json()


class UnconfiguredAnswerKeyClient:
    def set_answer_key(self, **kw):
        raise InternalServiceError("Test service is not configured.")


_client: AnswerKeyClient | None = None


def get_answer_key_client() -> AnswerKeyClient:
    global _client
    if _client is None:
        try:
            _client = HttpAnswerKeyClient()
        except InternalServiceError:
            return UnconfiguredAnswerKeyClient()
    return _client


def set_answer_key_client(c: AnswerKeyClient | None) -> None:
    global _client
    _client = c
