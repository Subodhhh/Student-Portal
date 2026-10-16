"""Client for the Student notification service (puts entries into students' existing inbox)."""
from typing import Protocol

from app.config import settings
from app.services.internal_http import InternalHttp, InternalRejected, InternalServiceError


class NotificationClient(Protocol):
    def send(self, *, dedupe_key: str, event_type: str, title: str, message: str, metadata: dict | None, student_ids: list[str]) -> None: ...


class HttpNotificationClient:
    def __init__(self) -> None:
        self._h = InternalHttp(settings.student_notification_service_url, settings.student_notification_service_api_key)

    def send(self, *, dedupe_key, event_type, title, message, metadata, student_ids) -> None:
        r = self._h.request("POST", "/notifications", json={"dedupe_key": dedupe_key, "event_type": event_type, "title": title,
                                                          "message": message, "metadata": metadata, "student_ids": student_ids})
        if r.status_code in (200, 201):
            return
        if r.status_code in (408, 429):
            raise InternalServiceError(f"notification service busy ({r.status_code})")
        raise InternalRejected(r.status_code)


class UnconfiguredNotificationClient:
    def send(self, **kw):
        raise InternalServiceError("Notification service is not configured.")


_client: NotificationClient | None = None


def get_notification_client() -> NotificationClient:
    global _client
    if _client is None:
        try:
            _client = HttpNotificationClient()
        except InternalServiceError:
            return UnconfiguredNotificationClient()
    return _client


def set_notification_client(c: NotificationClient | None) -> None:
    global _client
    _client = c
