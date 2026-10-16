"""Client for the Student service's internal API (the only way this backend creates or
reads student identities). Scoped service key, timeouts, bounded retries with
exponential backoff + jitter, and an idempotency key on writes."""
import random
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from institute_common.config import settings


class StudentServiceError(Exception):
    pass


@dataclass
class StudentSummary:
    student_id: str
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    status: str = "ACTIVE"

    def masked(self) -> dict:
        """Minimal disclosure used in match prompts (no email/phone)."""
        return {"student_id": self.student_id, "name": f"{self.first_name} {self.last_name[:1]}."}


class StudentClient(Protocol):
    def search(self, *, email: str | None = None, phone: str | None = None) -> list[StudentSummary]: ...
    def get(self, student_id: str) -> StudentSummary | None: ...
    def get_many(self, student_ids: list[str]) -> dict[str, StudentSummary]: ...
    def create(self, *, first_name: str, last_name: str, email: str, phone: str | None,
               idempotency_key: str) -> StudentSummary: ...


class HttpStudentClient:
    RETRIES = 3

    def __init__(self) -> None:
        if not settings.student_service_url or not settings.student_service_api_key:
            raise StudentServiceError("Student service is not configured.")
        self._http = httpx.Client(
            base_url=settings.student_service_url.rstrip("/") + "/internal/v1",
            headers={"X-Service-Key": settings.student_service_api_key, "X-Service-Name": "institute-backend"},
            timeout=httpx.Timeout(5.0, connect=2.0),
        )

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.RETRIES):
            try:
                r = self._http.request(method, path, **kw)
                if r.status_code < 500:
                    return r
                last = StudentServiceError(f"student service returned {r.status_code}")
            except httpx.TransportError as exc:
                last = exc
            time.sleep(min(2.0, 0.2 * 2**attempt) * (0.5 + random.random()))
        raise StudentServiceError("Student service unavailable") from last

    @staticmethod
    def _s(d: dict) -> StudentSummary:
        return StudentSummary(d["student_id"], d["first_name"], d["last_name"], d.get("email"), d.get("phone"),
                              d.get("status", "ACTIVE"))

    def search(self, *, email=None, phone=None):
        r = self._request("POST", "/students/search", json={"email": email, "phone": phone})
        if r.status_code != 200:
            raise StudentServiceError(f"search failed ({r.status_code})")
        return [self._s(x) for x in r.json()]

    def get(self, student_id):
        r = self._request("GET", f"/students/{student_id}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise StudentServiceError(f"lookup failed ({r.status_code})")
        return self._s(r.json())

    def get_many(self, student_ids):
        if not student_ids:
            return {}
        r = self._request("POST", "/students/batch-get", json={"student_ids": student_ids[:200]})
        if r.status_code != 200:
            raise StudentServiceError(f"batch lookup failed ({r.status_code})")
        return {x["student_id"]: self._s(x) for x in r.json()}

    def create(self, *, first_name, last_name, email, phone, idempotency_key):
        r = self._request(
            "POST", "/students",
            json={"first_name": first_name, "last_name": last_name, "email": email, "phone": phone},
            headers={"Idempotency-Key": idempotency_key},
        )
        if r.status_code not in (200, 201):
            raise StudentServiceError(f"create failed ({r.status_code})")
        return self._s(r.json())


class UnconfiguredStudentClient:
    """Resolved when no service URL/key is set: authorization still runs first, and any
    actual use reports 'unavailable' instead of crashing at dependency-resolution time."""

    def _fail(self, *a, **kw):
        raise StudentServiceError("Student service is not configured.")

    search = get = get_many = create = _fail


_client: StudentClient | None = None


def get_student_client() -> StudentClient:
    global _client
    if _client is None:
        try:
            _client = HttpStudentClient()
        except StudentServiceError:
            return UnconfiguredStudentClient()
    return _client


def set_student_client(client: StudentClient | None) -> None:
    global _client
    _client = client
