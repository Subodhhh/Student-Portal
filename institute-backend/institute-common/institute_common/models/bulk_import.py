"""Bulk question import (paper service): one job per uploaded Excel/Word file, one row per question in it, and the pictures it carries.

Nothing here is part of the Question Bank: a job's rows are a PREVIEW that the uploader corrects and confirms. Only the import step
(after confirmation) creates bank_questions / question_versions, row by row, and records the created question on the row.

Job status (the worker moves a job forward; users only queue work or cancel):

    UPLOADED -> PARSING -> PREVIEW <-> VALIDATE_QUEUED -> VALIDATING -> PREVIEW
    PREVIEW -> IMPORT_QUEUED -> IMPORTING -> COMPLETED | PARTIAL | FAILED
    UPLOADED / PREVIEW / VALIDATE_QUEUED -> CANCELLED          (user)
    PREVIEW (not touched for 7 days)     -> EXPIRED            (worker)

PARSING / VALIDATING / IMPORTING are held by one worker through a lease (`locked_until`); a crashed worker's lease runs out and
another worker resumes the job. Every step is idempotent, so resuming never duplicates rows, pictures or questions.
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from institute_common.database import Base, IPAddress, JSONType, UTCDateTime, utcnow

JOB_STATUSES = ("UPLOADED", "PARSING", "VALIDATE_QUEUED", "VALIDATING", "PREVIEW", "IMPORT_QUEUED", "IMPORTING",
                "COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "EXPIRED")
ROW_STATUSES = ("PENDING", "VALID", "INVALID", "DUPLICATE", "IMPORTED", "FAILED")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class BulkImportJob(Base):
    """One uploaded file being previewed or imported into one provider branch's bank, for one subject."""

    __tablename__ = "bulk_import_jobs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(JOB_STATUSES)})", name="bulk_import_jobs_status_check"),
        CheckConstraint("file_format IN ('XLSX','DOCX')", name="bulk_import_jobs_format_check"),
        # the import target: GLOBAL (platform bank: no provider, no branch), or PROVIDER with branch_id NULL (provider-wide) or set
        # (a branch import; the questions are still shared with every branch of the provider)
        CheckConstraint("(scope = 'GLOBAL' AND provider_id IS NULL AND branch_id IS NULL) OR (scope = 'PROVIDER' AND provider_id IS NOT NULL)",
                        name="bulk_import_jobs_scope_check"),
        Index("ix_bulk_import_jobs_branch_created", "branch_id", "created_at"),
        Index("ix_bulk_import_jobs_claim", "status", "locked_until"),
        Index("ix_bulk_import_jobs_owner_created", "scope", "provider_id", "created_at"),
    )

    job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="PROVIDER", server_default="PROVIDER")
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"), index=True)
    branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.branch_id"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="UPLOADED", server_default="UPLOADED")
    file_format: Mapped[str] = mapped_column(String(4), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)          # as uploaded; shown only, never used as a path
    source_key: Mapped[str | None] = mapped_column(String(300))                   # object-store key of the uploaded file (deleted when done)
    source_size: Mapped[int] = mapped_column(BigInteger, nullable=False)          # bytes (up to BULK_IMPORT_MAX_BYTES, 2 GiB)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # counts, refreshed after every stage (the preview, progress and final report read them)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    imported_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    file_errors: Mapped[list | None] = mapped_column(JSONType)                    # problems with the whole file (no row could be read)
    # Mappings an administrator confirmed for THIS import only: {"LEVEL|parent_id|value key": node_id}. Approved aliases are stored in
    # question_taxonomy_aliases instead and apply to every later import.
    taxonomy_mappings: Mapped[dict | None] = mapped_column(JSONType)
    # worker lease (who processes the job right now, and until when); attempts counts claims, so a job that keeps crashing stops
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    worker: Mapped[str | None] = mapped_column(String(120))
    last_error: Mapped[str | None] = mapped_column(String(500))                   # safe summary; details stay in the worker's log
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    created_ip: Mapped[str | None] = mapped_column(IPAddress)
    confirmed_by: Mapped[UUID | None] = mapped_column()
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    confirmed_ip: Mapped[str | None] = mapped_column(IPAddress)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow,
                                                 server_default=text("CURRENT_TIMESTAMP"))
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cleaned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)              # staged pictures and the source file deleted


class BulkImportRow(Base):
    """One question of an uploaded file, as read and as corrected by the uploader, with its validation result.

    `data` holds the template's text columns (chapter ... correct_option) exactly as they will be imported; `images` maps an image
    column to the SHA-256 of its picture (bulk_import_images); `read_errors` are problems found while reading the file (they survive
    corrections of the text); `errors` is the latest validation result."""

    __tablename__ = "bulk_import_rows"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(ROW_STATUSES)})", name="bulk_import_rows_status_check"),
        Index("ix_bulk_import_rows_job_status", "job_id", "status", "row_no"),
        Index("ix_bulk_import_rows_job_hash", "job_id", "content_hash"),
    )

    job_id: Mapped[UUID] = mapped_column(ForeignKey("bulk_import_jobs.job_id"), primary_key=True)
    row_no: Mapped[int] = mapped_column(Integer, primary_key=True)          # the row number the user sees in Excel / the Word table
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING", server_default="PENDING")
    data: Mapped[dict] = mapped_column(JSONType, nullable=False)
    images: Mapped[dict | None] = mapped_column(JSONType)
    read_errors: Mapped[list | None] = mapped_column(JSONType)
    errors: Mapped[list | None] = mapped_column(JSONType)
    content_hash: Mapped[str | None] = mapped_column(String(64))            # duplicate detection (same question text, options and pictures)
    chapter_id: Mapped[UUID | None] = mapped_column()                        # canonical taxonomy records found by validation
    topic_id: Mapped[UUID | None] = mapped_column()
    subtopic_id: Mapped[UUID | None] = mapped_column()
    question_id: Mapped[UUID | None] = mapped_column()                       # the bank question created by the import
    edited_by: Mapped[UUID | None] = mapped_column()
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class BulkImportImage(Base):
    """One distinct picture of a job (by SHA-256), staged in the object store until the import copies it into the provider's
    question pictures. Staged copies are deleted when the job ends (completed, failed, cancelled or expired)."""

    __tablename__ = "bulk_import_images"

    job_id: Mapped[UUID] = mapped_column(ForeignKey("bulk_import_jobs.job_id"), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    staging_key: Mapped[str] = mapped_column(String(300), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)


UPLOAD_STATUSES = ("RECEIVING", "COMPLETED", "DISCARDED")


class BulkImportUpload(Base):
    """A file being sent in pieces (resumable upload). The browser sends `chunk_size` pieces in order; each piece is stored in the
    object store at staging/bulk-uploads/<upload>/<part> as it arrives, and `received_parts` / `received_bytes` say how far it got,
    so an interrupted transfer (tab closed, network lost) continues from the next piece when the same file is chosen again
    (`fingerprint`: the browser's name|size|last-modified of the file, with the same target and subject). When every byte is in, the
    pieces become the job's source file and the job is created exactly like a one-request upload (status COMPLETED, `job_id`).
    Sessions not continued for BULK_UPLOAD_RESUME_DAYS are DISCARDED by the worker and their pieces deleted."""

    __tablename__ = "bulk_import_uploads"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(UPLOAD_STATUSES)})", name="bulk_import_uploads_status_check"),
        CheckConstraint("file_format IN ('XLSX','DOCX')", name="bulk_import_uploads_format_check"),
        CheckConstraint("(scope = 'GLOBAL' AND provider_id IS NULL) OR (scope = 'PROVIDER' AND provider_id IS NOT NULL)",
                        name="bulk_import_uploads_target_check"),
        CheckConstraint("received_bytes >= 0 AND received_bytes <= size AND received_parts >= 0 AND chunk_size > 0 AND size > 0",
                        name="bulk_import_uploads_progress_check"),
        Index("ix_bulk_import_uploads_owner_status", "created_by", "status"),
        Index("ix_bulk_import_uploads_status_updated", "status", "updated_at"),
    )

    upload_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    file_format: Mapped[str] = mapped_column(String(4), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)          # shown only, never used as a path
    fingerprint: Mapped[str] = mapped_column(String(300), nullable=False)        # the browser's name|size|last-modified (resume matching)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    received_parts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="RECEIVING", server_default="RECEIVING")
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("bulk_import_jobs.job_id"))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class PaperNotificationOutbox(Base):
    """Staff notifications raised by the paper service (bulk upload preview ready / import finished), delivered to the operations
    service (owner of the inbox) by the bulk-import worker with retries - the exam service's pattern (exam_notification_outbox)."""

    __tablename__ = "paper_notification_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','SENT','FAILED')", name="paper_notification_outbox_status_check"),
        Index("ix_paper_notification_outbox_due", "status", "next_attempt_at"),
    )

    outbox_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
