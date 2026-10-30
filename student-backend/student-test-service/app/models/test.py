from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Test(Base):
    __tablename__ = "tests"

    test_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    provider_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    branch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    # Set for a unified Global Test exam (one per student per date, papers from several providers): shown as the Global Test, not as
    # the provider/branch above, which is only the Institute side's anchor.
    global_occurrence_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,     # the occurrence ranking scans by it
    )

    # Unified Global Test: every configured subject in order, with its delivered question count or why there is no paper.
    sections: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),     # jsonb: the listings use SELECT DISTINCT (plain json has no equality)
        nullable=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    duration_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    total_questions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    total_marks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    start_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    end_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        Enum(
            "DRAFT",
            "PUBLISHED",
            "ACTIVE",
            "INACTIVE",
            "COMPLETED",
            name="test_status",
        ),
        nullable=False,
        default="DRAFT",
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