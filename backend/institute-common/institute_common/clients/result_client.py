"""Client for the Student result service (internal API, service key): reads results and applies corrections."""
from typing import Protocol

from institute_common.config import settings
from institute_common.internal_http import InternalHttp, InternalServiceError


class ResultServiceError(Exception):
    """The result service is unreachable, unconfigured or failed (5xx) after the retries."""


class ResultRejected(Exception):
    """The result service refused the request (4xx); repeating it cannot help."""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"rejected ({status}) {detail}".strip())
        self.status = status
        self.detail = detail


class ResultClient(Protocol):
    def exam_results(self, test_id: str) -> list[dict]: ...
    def adjust(self, *, test_id: str, student_id: str, obtained_marks: int, reason: str, actor: str, correction_ref: str) -> dict: ...
    def reevaluate(self, *, test_id: str, reason: str, actor: str, correction_ref: str) -> dict: ...


class HttpResultClient:
    def __init__(self) -> None:
        try:
            self._h = InternalHttp(settings.student_result_service_url, settings.student_result_service_api_key)
        except InternalServiceError as exc:
            raise ResultServiceError("Result service is not configured.") from exc

    def _call(self, method: str, path: str, **kw):
        try:
            r = self._h.request(method, path, **kw)
        except InternalServiceError as exc:
            raise ResultServiceError("Result service unavailable") from exc
        if r.status_code >= 400:
            raise ResultRejected(r.status_code, _detail(r))
        return r.json()

    def exam_results(self, test_id: str) -> list[dict]:
        try:
            return self._call("GET", f"/tests/{test_id}/results")
        except ResultRejected as exc:
            raise ResultServiceError(f"result service refused the request ({exc.status})") from exc

    def adjust(self, *, test_id, student_id, obtained_marks, reason, actor, correction_ref) -> dict:
        return self._call("POST", "/results/adjust", json={"test_id": test_id, "student_id": student_id, "obtained_marks": obtained_marks,
                                                           "reason": reason, "actor": actor, "correction_ref": correction_ref})

    def reevaluate(self, *, test_id, reason, actor, correction_ref) -> dict:
        return self._call("POST", f"/tests/{test_id}/reevaluate", json={"reason": reason, "actor": actor, "correction_ref": correction_ref})


def _detail(r) -> str:
    try:
        d = r.json().get("detail", "")
        return d if isinstance(d, str) else str(d)[:300]
    except Exception:
        return ""


class UnconfiguredResultClient:
    def exam_results(self, test_id):
        raise ResultServiceError("Result service is not configured.")

    def adjust(self, **kw):
        raise ResultServiceError("Result service is not configured.")

    def reevaluate(self, **kw):
        raise ResultServiceError("Result service is not configured.")


_client: ResultClient | None = None


def get_result_client() -> ResultClient:
    global _client
    if _client is None:
        try:
            _client = HttpResultClient()
        except ResultServiceError:
            return UnconfiguredResultClient()
    return _client


def set_result_client(c: ResultClient | None) -> None:
    global _client
    _client = c
