from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EnrollmentBatch(Base):
    __tablename__ = "enrollment_batches"

    enrollment_batch_id: Mapped[UUID] = mapped_column(
        primary_key=True,
    )

    enrollment_id: Mapped[str] = mapped_column(
        ForeignKey("enrollments.enrollment_id"),
        nullable=False,
    )

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("batches.batch_id"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )