import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SQLEnum
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class NotificationEventType(str, Enum):
    TEST_SCHEDULED = "TEST_SCHEDULED"
    TEST_REMINDER = "TEST_REMINDER"
    TEST_STARTED = "TEST_STARTED"
    TEST_SUBMITTED = "TEST_SUBMITTED"
    TEST_TIMEOUT = "TEST_TIMEOUT"
    RESULT_PUBLISHED = "RESULT_PUBLISHED"
    RANKING_UPDATED = "RANKING_UPDATED"
    SYSTEM_ANNOUNCEMENT = "SYSTEM_ANNOUNCEMENT"
    SYSTEM_MAINTENANCE = "SYSTEM_MAINTENANCE"


class RecipientType(str, Enum):
    STUDENT = "STUDENT"
    INSTITUTE = "INSTITUTE"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class Notification(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    event_type: Mapped[NotificationEventType] = mapped_column(
        SQLEnum(
            NotificationEventType,
            name="notification_event_type",
        ),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    recipients: Mapped[list["NotificationRecipient"]] = relationship(
        back_populates="notification",
        cascade="all, delete-orphan",
    )


class NotificationRecipient(Base):
    __tablename__ = "notification_recipients"

    notification_recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notifications.notification_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    recipient_type: Mapped[RecipientType] = mapped_column(
        SQLEnum(
            RecipientType,
            name="notification_recipient_type",
        ),
        nullable=False,
        index=True,
    )

    recipient_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    notification: Mapped["Notification"] = relationship(
        back_populates="recipients",
    )