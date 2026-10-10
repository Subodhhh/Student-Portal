from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.notification import (
    NotificationEventType,
    RecipientType,
)


class NotificationRecipientRequest(BaseModel):
    recipient_type: RecipientType
    recipient_id: str


class CreateNotificationRequest(BaseModel):
    event_type: NotificationEventType
    title: str
    message: str
    metadata: dict | None = None
    recipients: list[NotificationRecipientRequest]


class NotificationRecipientResponse(BaseModel):
    notification_recipient_id: UUID
    recipient_type: RecipientType
    recipient_id: str
    is_read: bool
    read_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class NotificationResponse(BaseModel):
    notification_id: UUID
    event_type: NotificationEventType
    title: str
    message: str
    metadata: dict | None
    created_at: datetime
    recipient: NotificationRecipientResponse

    model_config = ConfigDict(from_attributes=True)


class UnreadNotificationCountResponse(BaseModel):
    unread_count: int


class MarkNotificationReadResponse(BaseModel):
    notification_recipient_id: UUID
    is_read: bool
    read_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MarkAllNotificationsReadResponse(BaseModel):
    updated_count: int