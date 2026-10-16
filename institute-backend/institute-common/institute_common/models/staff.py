from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from institute_common.database import Base, IPAddress, JSONType, UTCDateTime, utcnow


class Staff(Base):
    """Global staff identity. staff_code is immutable and unique (e.g. STF000001)."""

    __tablename__ = "staff"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="staff_status_check"),
    )

    staff_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    staff_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column()
    created_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    updated_by: Mapped[UUID | None] = mapped_column()
    updated_ip_address: Mapped[str | None] = mapped_column(IPAddress)
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class StaffToken(Base):
    """Email-verification and password-reset tokens (only the SHA-256 hash is stored)."""

    __tablename__ = "staff_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('EMAIL_VERIFY', 'PASSWORD_RESET')", name="staff_tokens_purpose_check"),
    )

    token_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


_GLOBAL = text("provider_id IS NULL")
_PROVIDER = text("provider_id IS NOT NULL AND branch_id IS NULL")
_BRANCH = text("branch_id IS NOT NULL")


class StaffRoleAssignment(Base):
    """A role held by a global staff identity within one scope."""

    __tablename__ = "staff_role_assignments"
    __table_args__ = (
        CheckConstraint(
            "role IN ('SUPER_ADMIN','ADMIN','PROVIDER_ADMIN','BRANCH_ADMIN','TEACHER')",
            name="staff_role_assignments_role_check",
        ),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="staff_role_assignments_status_check"),
        CheckConstraint(
            "(role IN ('SUPER_ADMIN','ADMIN') AND provider_id IS NULL AND branch_id IS NULL) "
            "OR (role IN ('PROVIDER_ADMIN','TEACHER') AND provider_id IS NOT NULL AND branch_id IS NULL) "
            "OR (role = 'BRANCH_ADMIN' AND provider_id IS NOT NULL AND branch_id IS NOT NULL)",
            name="staff_role_assignments_scope_check",
        ),
        Index("uq_sra_global", "staff_id", "role", unique=True,
              postgresql_where=_GLOBAL, sqlite_where=_GLOBAL),
        Index("uq_sra_provider", "staff_id", "role", "provider_id", unique=True,
              postgresql_where=_PROVIDER, sqlite_where=_PROVIDER),
        Index("uq_sra_branch", "staff_id", "role", "provider_id", "branch_id", unique=True,
              postgresql_where=_BRANCH, sqlite_where=_BRANCH),
    )

    assignment_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"))
    branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.branch_id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class AuditLog(Base):
    """Append-only. The application never updates or deletes rows; the migration
    adds a PostgreSQL trigger that rejects UPDATE/DELETE."""

    __tablename__ = "institute_audit_logs"

    audit_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, index=True, server_default=text("CURRENT_TIMESTAMP"))
    actor_staff_id: Mapped[UUID | None] = mapped_column(index=True)
    actor_service: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(64))
    provider_id: Mapped[UUID | None] = mapped_column(index=True)
    branch_id: Mapped[UUID | None] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)  # SUCCESS | DENIED | FAILURE
    reason: Mapped[str | None] = mapped_column(String(1000))
    ip_address: Mapped[str | None] = mapped_column(IPAddress)
    details: Mapped[dict | None] = mapped_column(JSONType)
