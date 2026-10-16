import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_student
from app.config import settings
from app.database import get_db
from app.models.notification import RecipientType
from app.schemas.notification import (
    CreateNotificationRequest,
    MarkAllNotificationsReadResponse,
    MarkNotificationReadResponse,
    NotificationResponse,
    UnreadNotificationCountResponse,
)
from app.services.notification_service import (
    create_system_notification,
    get_recipient_notifications,
    get_recipient_unread_count,
    mark_recipient_notification_as_read,
    mark_recipient_notifications_as_read,
)


router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)


def require_create_key(
    x_service_key: str | None = Header(default=None),
) -> None:
    """Require a configured service key for notification creation."""
    keys = [
        key.strip()
        for key in settings.notification_create_api_keys.split(",")
        if key.strip()
    ]

    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Notification creation is not configured",
        )

    if not x_service_key or not any(
        hmac.compare_digest(x_service_key, key) for key in keys
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service credentials",
        )


@router.post(
    "",
    dependencies=[Depends(require_create_key)],
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_notification(
    data: CreateNotificationRequest,
    db: Session = Depends(get_db),
):
    notification = create_system_notification(
        db=db,
        data=data,
    )

    return {
        "notification_id": notification.notification_id,
        "event_type": notification.event_type,
        "title": notification.title,
        "message": notification.message,
        "metadata": notification.event_metadata,
        "created_at": notification.created_at,
        "recipient": notification.recipients[0],
    }


@router.get(
    "",
    response_model=list[NotificationResponse],
)
def get_notifications(
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return get_recipient_notifications(
        db=db,
        recipient_id=student_id,
        recipient_type=RecipientType.STUDENT,
    )


@router.get(
    "/unread-count",
    response_model=UnreadNotificationCountResponse,
)
def get_unread_notification_count(
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    unread_count = get_recipient_unread_count(
        db=db,
        recipient_id=student_id,
        recipient_type=RecipientType.STUDENT,
    )

    return {
        "unread_count": unread_count,
    }


@router.patch(
    "/{notification_recipient_id}/read",
    response_model=MarkNotificationReadResponse,
)
def mark_notification_read(
    notification_recipient_id: UUID,
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    return mark_recipient_notification_as_read(
        db=db,
        notification_recipient_id=notification_recipient_id,
        recipient_id=student_id,
        recipient_type=RecipientType.STUDENT,
    )


@router.patch(
    "/read-all",
    response_model=MarkAllNotificationsReadResponse,
)
def mark_all_notifications_read(
    student_id: str = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    updated_count = mark_recipient_notifications_as_read(
        db=db,
        recipient_id=student_id,
        recipient_type=RecipientType.STUDENT,
    )

    return {
        "updated_count": updated_count,
    }