"""Mappings of tables that already exist in the shared database.

providers / branches / batches / enrollments / enrollment_batches were created
by the enrollment service's migration chain (version table m3_alembic_version).
They are mapped here with the same columns and are NEVER created or altered by
this service's migrations. Column sets mirror student-enrollment-service models;
anything that differs in the live database is a flagged blocker, not a guess.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from institute_common.database import Base, IPAddress, UTCDateTime, utcnow

ACTIVE_STATES = ("ACTIVE", "INACTIVE")


class Provider(Base):
    __tablename__ = "providers"

    provider_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum(*ACTIVE_STATES, name="provider_status", create_type=False), nullable=False, default="ACTIVE"
    )
    created_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    created_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    created_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    updated_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    updated_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    updated_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    deleted_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    deleted_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Branch(Base):
    __tablename__ = "branches"

    branch_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(
        Enum(*ACTIVE_STATES, name="branch_status", create_type=False), nullable=False, default="ACTIVE"
    )
    created_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    created_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    created_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    updated_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    updated_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    updated_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    deleted_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    deleted_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Batch(Base):
    __tablename__ = "batches"

    batch_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        Enum(*ACTIVE_STATES, name="batch_status", create_type=False), nullable=False, default="ACTIVE"
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class StudentRef(Base):
    """Legacy mapping of the student-owned table. Student identities live in the Student database (Neon branch subodh_dev) and are
    reached only through the Student authentication service's internal API; nothing here references this table any more."""

    __tablename__ = "students"

    student_id: Mapped[str] = mapped_column(String(20), primary_key=True)


class Enrollment(Base):
    __tablename__ = "enrollments"

    enrollment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    # Global Student ID (STUnnnnnn) owned by the Student service in another database: no foreign key (migration 0002_enrollment).
    student_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    enrolled_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(
        Enum(*ACTIVE_STATES, name="enrollment_status", create_type=False), nullable=False, default="ACTIVE"
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class EnrollmentBatch(Base):
    __tablename__ = "enrollment_batches"

    enrollment_batch_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("enrollments.enrollment_id"), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("batches.batch_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
