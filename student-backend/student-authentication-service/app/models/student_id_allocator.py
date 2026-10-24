from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StudentIdAllocator(Base):
    """The durable Student ID counter (one row, name 'STU'): `last_value` is the highest number ever issued. Never lowered and never
    derived from the students table, so a deleted student's ID is never issued to anyone else (see app/services/student_ids.py)."""

    __tablename__ = "student_id_allocator"
    __table_args__ = (CheckConstraint("last_value >= 0", name="student_id_allocator_nonnegative"),)

    name: Mapped[str] = mapped_column(String(10), primary_key=True)
    last_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
                                                 onupdate=lambda: datetime.now(timezone.utc), server_default=text("CURRENT_TIMESTAMP"))
