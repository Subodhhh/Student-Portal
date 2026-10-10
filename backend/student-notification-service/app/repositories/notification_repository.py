from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.models.notification import (
    Notification,
    NotificationRecipient,
)


def create_notification(
    db: Session,
    notification: Notification,
) -> Notification:
    db.add(notification)
    db.commit()
    db.refresh(notification)

    return notification


def get_notifications_for_recipient(
    db: Session,
    recipient_id: str,
    recipient_type,
) -> list[NotificationRecipient]:
    query = (
        select(NotificationRecipient)
        .join(NotificationRecipient.notification)
        .where(
            NotificationRecipient.recipient_id == recipient_id,
            NotificationRecipient.recipient_type == recipient_type,
        )
        .options(joinedload(NotificationRecipient.notification))
        .order_by(
            NotificationRecipient.is_read.asc(),
            Notification.created_at.desc(),
        )
    )

    return list(
        db.execute(query).scalars().unique().all()
    )


def get_unread_count(
    db: Session,
    recipient_id: str,
    recipient_type,
) -> int:
    query = select(
        func.count(NotificationRecipient.notification_recipient_id)
    ).where(
        NotificationRecipient.recipient_id == recipient_id,
        NotificationRecipient.recipient_type == recipient_type,
        NotificationRecipient.is_read.is_(False),
    )

    return db.scalar(query) or 0


def get_notification_recipient(
    db: Session,
    notification_recipient_id: UUID,
    recipient_id: str,
    recipient_type,
) -> NotificationRecipient | None:
    query = select(NotificationRecipient).where(
        NotificationRecipient.notification_recipient_id
        == notification_recipient_id,
        NotificationRecipient.recipient_id == recipient_id,
        NotificationRecipient.recipient_type == recipient_type,
    )

    return db.scalar(query)


def mark_notification_as_read(
    db: Session,
    notification_recipient: NotificationRecipient,
) -> NotificationRecipient:
    notification_recipient.is_read = True
    notification_recipient.read_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(notification_recipient)

    return notification_recipient


def mark_all_notifications_as_read(
    db: Session,
    recipient_id: str,
    recipient_type,
) -> int:
    query = (
        update(NotificationRecipient)
        .where(
            NotificationRecipient.recipient_id == recipient_id,
            NotificationRecipient.recipient_type == recipient_type,
            NotificationRecipient.is_read.is_(False),
        )
        .values(
            is_read=True,
            read_at=datetime.now(timezone.utc),
        )
    )

    result = db.execute(query)

    db.commit()

    return result.rowcount