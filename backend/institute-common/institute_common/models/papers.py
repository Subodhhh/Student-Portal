from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from institute_common.database import Base, IPAddress, JSONType, UTCDateTime, utcnow


class StoredFile(Base):
    """Metadata for a privately stored upload. Bytes never live in PostgreSQL and are
    only reachable through an authorized endpoint."""

    __tablename__ = "stored_files"

    file_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class Question(Base):
    """Question-bank identity. Content lives in immutable question_versions.

    Named bank_questions because the Student test service already owns a table called `questions`
    (the questions of one delivered test) in the same shared database."""

    __tablename__ = "bank_questions"

    question_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    owner_staff_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (
        UniqueConstraint("question_id", "version_no", name="uq_question_version"),
        CheckConstraint("correct_option IN ('A','B','C','D')", name="question_versions_correct_check"),
        CheckConstraint("marks_positive > 0 AND marks_negative >= 0", name="question_versions_marks_check"),
    )

    question_version_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    question_id: Mapped[UUID] = mapped_column(ForeignKey("bank_questions.question_id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(Text, nullable=False)
    option_b: Mapped[str] = mapped_column(Text, nullable=False)
    option_c: Mapped[str] = mapped_column(Text, nullable=False)
    option_d: Mapped[str] = mapped_column(Text, nullable=False)
    correct_option: Mapped[str] = mapped_column(String(1), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(200))
    marks_positive: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    marks_negative: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=0, server_default="0")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class PaperUpload(Base):
    __tablename__ = "paper_uploads"
    __table_args__ = (
        CheckConstraint("status IN ('VALIDATED','REJECTED')", name="paper_uploads_status_check"),
    )

    upload_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("stored_files.file_id"), nullable=False)
    uploaded_by: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    question_count: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[list | None] = mapped_column(JSONType)
    paper_version_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    uploaded_ip: Mapped[str | None] = mapped_column(IPAddress)


class Paper(Base):
    __tablename__ = "papers"

    paper_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class PaperVersion(Base):
    """Immutable once APPROVED: changes require a new version."""

    __tablename__ = "paper_versions"
    __table_args__ = (
        UniqueConstraint("paper_id", "version_no", name="uq_paper_version"),
        CheckConstraint("status IN ('VALIDATED','APPROVED','PUBLISHED','SUPERSEDED','WITHDRAWN')",
                        name="paper_versions_status_check"),
    )

    paper_version_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    paper_id: Mapped[UUID] = mapped_column(ForeignKey("papers.paper_id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="VALIDATED", server_default="VALIDATED")
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PaperVersionQuestion(Base):
    __tablename__ = "paper_version_questions"
    __table_args__ = (UniqueConstraint("paper_version_id", "position", name="uq_pvq_position"),)

    pvq_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    paper_version_id: Mapped[UUID] = mapped_column(ForeignKey("paper_versions.paper_version_id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question_version_id: Mapped[UUID] = mapped_column(ForeignKey("question_versions.question_version_id"), nullable=False)


class PaperVersionEvent(Base):
    """Append-only status history (approvals, publication, overrides)."""

    __tablename__ = "paper_version_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    paper_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    actor_staff_id: Mapped[UUID] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class PaperShare(Base):
    """Owner provider authorizes another provider to use a published paper version
    in its exams (composite tests). Grants use, never access to question content."""

    __tablename__ = "paper_shares"
    __table_args__ = (UniqueConstraint("paper_id", "shared_with_provider_id", name="uq_paper_share"),)

    share_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    paper_id: Mapped[UUID] = mapped_column(ForeignKey("papers.paper_id"), nullable=False)
    owner_provider_id: Mapped[UUID] = mapped_column(nullable=False)
    shared_with_provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    granted_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
