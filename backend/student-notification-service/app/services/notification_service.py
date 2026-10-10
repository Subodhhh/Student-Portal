from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.notification import (
    Notification,
    NotificationRecipient,
)
from app.repositories.notification_repository import (
    create_notification,
    get_notification_recipient,
    get_notifications_for_recipient,
    get_unread_count,
    mark_all_notifications_as_read,
    mark_notification_as_read,
)
from app.schemas.notification import (
    CreateNotificationRequest,
)


def create_system_notification(
    db: Session,
    data: CreateNotificationRequest,
) -> Notification:
    notification = Notification(
        event_type=data.event_type,
        title=data.title,
        message=data.message,
        event_metadata=data.metadata,
    )

    for recipient in data.recipients:
        notification.recipients.append(
            NotificationRecipient(
                recipient_type=recipient.recipient_type,
                recipient_id=recipient.recipient_id,
            )
        )

    return create_notification(
        db=db,
        notification=notification,
    )


def get_recipient_notifications(
    db: Session,
    recipient_id: str,
    recipient_type,
):
    recipients = get_notifications_for_recipient(
        db=db,
        recipient_id=recipient_id,
        recipient_type=recipient_type,
    )

    return [
        {
            "notification_id": recipient.notification.notification_id,
            "event_type": recipient.notification.event_type,
            "title": recipient.notification.title,
            "message": recipient.notification.message,
            "metadata": recipient.notification.event_metadata,
            "created_at": recipient.notification.created_at,
            "recipient": recipient,
        }
        for recipient in recipients
    ]


def get_recipient_unread_count(
    db: Session,
    recipient_id: str,
    recipient_type,
) -> int:
    return get_unread_count(
        db=db,
        recipient_id=recipient_id,
        recipient_type=recipient_type,
    )


def mark_recipient_notification_as_read(
    db: Session,
    notification_recipient_id: UUID,
    recipient_id: str,
    recipient_type,
):
    notification_recipient = get_notification_recipient(
        db=db,
        notification_recipient_id=notification_recipient_id,
        recipient_id=recipient_id,
        recipient_type=recipient_type,
    )

    if not notification_recipient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    if notification_recipient.is_read:
        return notification_recipient

    return mark_notification_as_read(
        db=db,
        notification_recipient=notification_recipient,
    )


def mark_recipient_notifications_as_read(
    db: Session,
    recipient_id: str,
    recipient_type,
) -> int:
    return mark_all_notifications_as_read(
        db=db,
        recipient_id=recipient_id,
        recipient_type=recipient_type,
    )