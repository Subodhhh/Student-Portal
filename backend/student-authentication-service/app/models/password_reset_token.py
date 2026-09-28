from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PasswordSetupResetToken(Base):
    __tablename__ = "password_setup_reset_tokens"

    token_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("students.student_id"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_type: Mapped[str] = mapped_column(
        Enum(
            "PASSWORD_SETUP",
            "PASSWORD_RESET",
            name="password_setup_reset_token_type",
            create_type=False,
        ),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_latitude: Mapped[float | None] = mapped_column(nullable=True)
    created_longitude: Mapped[float | None] = mapped_column(nullable=True)

    updated_ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    updated_latitude: Mapped[float | None] = mapped_column(nullable=True)
    updated_longitude: Mapped[float | None] = mapped_column(nullable=True)

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