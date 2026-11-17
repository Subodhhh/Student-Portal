from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, deferred, mapped_column

from institute_common.core.taxonomy import DIFFICULTIES, PATTERNS, QUESTION_TYPES, sql_in
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


class QuestionImageAsset(Base):
    """One picture used by questions or options, stored once per provider (deduplicated by its SHA-256) and referenced by asset id.

    `storage` names where the bytes live. Today it is always "db": the bytes are in `image_data` (BYTEA). A future object store adds a
    new storage kind with a key, without changing the question tables that reference assets. `image_data` is deferred: listing
    or loading questions never reads picture bytes. Bytes are served only by the authorised image endpoints."""

    __tablename__ = "question_image_assets"
    __table_args__ = (
        UniqueConstraint("provider_id", "checksum", name="uq_question_image_assets_provider_checksum"),
        CheckConstraint("storage IN ('db')", name="question_image_assets_storage_check"),
        CheckConstraint("file_size > 0", name="question_image_assets_size_check"),
    )

    asset_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)                 # SHA-256 of the original bytes
    storage: Mapped[str] = mapped_column(String(10), nullable=False, default="db", server_default="db")
    image_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    created_ip: Mapped[str | None] = mapped_column(IPAddress)


class PictureRecognitionJob(Base):
    """One recogniser's work on one picture (the durable job record; Redis only wakes workers up). Workers claim the jobs of the
    recognisers they have loaded, so CPU workers (local) and GPU workers (production) share one queue by configuration.

    status: QUEUED -> RUNNING -> DONE | FAILED. A RUNNING job whose lock expired (a crashed worker) is claimed again; after
    `max attempts` it is FAILED with its last error. `version` is the recogniser's model version: a new model re-runs pictures."""

    __tablename__ = "picture_recognition_jobs"
    __table_args__ = (
        UniqueConstraint("asset_id", "recognizer", "version", name="uq_picture_recognition_jobs_asset_recognizer_version"),
        CheckConstraint("status IN ('QUEUED','RUNNING','DONE','FAILED')", name="picture_recognition_jobs_status_check"),
        Index("ix_picture_recognition_jobs_claim", "recognizer", "status", "locked_until"),
    )

    job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("question_image_assets.asset_id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    recognizer: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="QUEUED", server_default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    worker: Mapped[str | None] = mapped_column(String(120))
    result: Mapped[dict | None] = mapped_column(JSONType)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, onupdate=utcnow)


# question_versions column holding the picture of each field (None: the field has no picture)
IMAGE_FIELD_COLUMNS = {"question": "stem_image_id", "A": "option_a_image_id", "B": "option_b_image_id", "C": "option_c_image_id",
                       "D": "option_d_image_id"}


class Question(Base):
    """Question-bank identity. Content lives in immutable question_versions.

    Named bank_questions because the Student test service already owns a table called `questions`
    (the questions of one delivered test) in the same shared database."""

    __tablename__ = "bank_questions"
    __table_args__ = (
        # GLOBAL questions belong to the platform catalogue (no provider/branch); PROVIDER questions to one provider's branch.
        CheckConstraint("(scope = 'GLOBAL' AND provider_id IS NULL AND branch_id IS NULL) OR "
                        "(scope = 'PROVIDER' AND provider_id IS NOT NULL AND branch_id IS NOT NULL)", name="bank_questions_scope_check"),
        CheckConstraint(f"difficulty IS NULL OR difficulty IN ({sql_in(DIFFICULTIES)})", name="bank_questions_difficulty_check"),
        CheckConstraint(f"pattern IN ({sql_in(PATTERNS)})", name="bank_questions_pattern_check"),
        CheckConstraint(f"question_type IN ({sql_in(QUESTION_TYPES)})", name="bank_questions_type_check"),
        Index("ix_bank_questions_catalogue", "scope", "subject_id", "status"),
        Index("ix_bank_questions_taxonomy", "subject_id", "exam_type", "target_class", "chapter"),
        Index("ix_bank_questions_branch_id", "branch_id"),
    )

    question_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="PROVIDER", server_default="PROVIDER")
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"), index=True)
    branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.branch_id"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    owner_staff_id: Mapped[UUID] = mapped_column(nullable=False)
    # Set on a provider's own copy made when it corrected a question it does not own (platform / other branch / other teacher).
    forked_from_question_id: Mapped[UUID | None] = mapped_column(ForeignKey("bank_questions.question_id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    # Classification (mutable metadata: re-classifying never changes question content, which lives in immutable versions).
    exam_type: Mapped[str | None] = mapped_column(String(80))
    target_class: Mapped[str | None] = mapped_column(String(80))
    chapter: Mapped[str | None] = mapped_column(String(80))
    topic: Mapped[str | None] = mapped_column(String(200))
    subtopic: Mapped[str | None] = mapped_column(String(80))
    difficulty: Mapped[str | None] = mapped_column(String(10))
    pattern: Mapped[str] = mapped_column(String(30), nullable=False, default="SINGLE_CORRECT", server_default="SINGLE_CORRECT")
    subtype: Mapped[str | None] = mapped_column(String(80))
    question_type: Mapped[str] = mapped_column(String(20), nullable=False, default="MCQ_SINGLE", server_default="MCQ_SINGLE")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, onupdate=utcnow)


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
    # pictures (question_image_assets); a field may have text, a picture or (the question) both. Text columns stay NOT NULL: a
    # picture-only option stores "" as its text.
    stem_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_a_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_b_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_c_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_d_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class PaperUpload(Base):
    __tablename__ = "paper_uploads"
    __table_args__ = (
        Index("ix_paper_uploads_branch_created", "branch_id", "created_at"),
        CheckConstraint("status IN ('VALIDATED','REJECTED')", name="paper_uploads_status_check"),
    )

    upload_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("stored_files.file_id"), nullable=False)
    answer_key_file_id: Mapped[UUID | None] = mapped_column(ForeignKey("stored_files.file_id"))   # separate answer-key upload, if used
    title: Mapped[str | None] = mapped_column(String(255))
    uploaded_by: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    question_count: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[list | None] = mapped_column(JSONType)
    # Scientific-notation recognition of every field: original text, stored text, outcome, reasons, the provider's decisions and the
    # engine version (services/recognition.record). NULL for uploads made before recognition existed or rejected before it ran.
    recognition: Mapped[dict | None] = mapped_column(JSONType)
    paper_version_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    uploaded_ip: Mapped[str | None] = mapped_column(IPAddress)


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        Index("ix_papers_branch_created", "branch_id", "created_at"),
        # GLOBAL papers are platform content built from global questions (used by Global Tests and, read-only, by providers).
        CheckConstraint("(scope = 'GLOBAL' AND provider_id IS NULL AND branch_id IS NULL) OR "
                        "(scope = 'PROVIDER' AND provider_id IS NOT NULL AND branch_id IS NOT NULL)", name="papers_scope_check"),
    )

    paper_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="PROVIDER", server_default="PROVIDER")
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"), index=True)
    branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.branch_id"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    exam_type: Mapped[str | None] = mapped_column(String(80))
    target_class: Mapped[str | None] = mapped_column(String(80))
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
    __table_args__ = (Index("ix_pvq_question_version", "question_version_id"), UniqueConstraint("paper_version_id", "position", name="uq_pvq_position"),)

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


class GlobalPaperCopy(Base):
    """Idempotency record for an authorized provider-to-platform paper copy."""

    __tablename__ = "global_paper_copies"
    __table_args__ = (
        UniqueConstraint(
            "contribution_id",
            name="uq_global_paper_copy_contribution",
        ),
    )

    copy_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contribution_id: Mapped[UUID] = mapped_column(nullable=False)
    source_paper_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_versions.paper_version_id"),
        nullable=False,
    )
    global_paper_id: Mapped[UUID] = mapped_column(
        ForeignKey("papers.paper_id"),
        nullable=False,
    )
    global_paper_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("paper_versions.paper_version_id"),
        nullable=False,
    )
    authorization_reference: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    copied_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class PhotoDraft(Base):
    """A paper being built from mobile photos: page photos and answer-key photos (or a key file) are uploaded separately, OCR'd page by
    page, parsed into an editable preview and accepted only by an explicit confirmation that passes the normal all-or-nothing
    validator. `extracted` keeps the OCR result exactly as read; `questions` is the corrected version; `corrections` logs every change.
    Acceptance creates an ordinary VALIDATED paper version (then the usual approval)."""

    __tablename__ = "paper_photo_drafts"
    __table_args__ = (CheckConstraint("status IN ('DRAFT','ACCEPTED','DISCARDED')", name="paper_photo_drafts_status_check"),)

    draft_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False, index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT", server_default="DRAFT")
    client_request_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    questions: Mapped[list | None] = mapped_column(JSONType)
    extracted: Mapped[list | None] = mapped_column(JSONType)
    issues: Mapped[list | None] = mapped_column(JSONType)
    corrections: Mapped[list | None] = mapped_column(JSONType)
    extracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    accepted_paper_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("paper_versions.paper_version_id"))
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class PhotoDraftFile(Base):
    """One uploaded photo (or answer-key file) of a draft, in page order, with its OCR lines ([text, weakest word confidence, mean])."""

    __tablename__ = "paper_photo_draft_files"
    __table_args__ = (
        UniqueConstraint("draft_id", "kind", "position", name="uq_photo_draft_file_position"),
        CheckConstraint("kind IN ('PAGE','ANSWER_KEY')", name="paper_photo_draft_files_kind_check"),
    )

    draft_file_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("paper_photo_drafts.draft_id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("stored_files.file_id"), nullable=False)
    ocr_lines: Mapped[list | None] = mapped_column(JSONType)
    ocr_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


_OPEN_IMPORT = text("status = 'OPEN' AND kind = 'IMPORT'")
_OPEN_CORRECTION = text("status = 'OPEN' AND kind = 'CORRECTION'")


class PaperReviewDraft(Base):
    """A provider's recognition review in progress, saved as it is made so it survives a reload and can be resumed.

    IMPORT: the uploaded question file (and answer key) are kept as stored_files, so the review can be reopened without choosing
    the file again; `review_token` binds the decisions to that file, its key, the subject domain and the engine version.
    CORRECTION: a correction being prepared on an existing paper version (`paper_version_id`).
    `decisions` maps "<question number>:<field>" to {"decision": {...} | null, "rev": n, "by": staff_id, "at": iso time}: every key
    remembers the draft revision that last changed it, so two people saving different fields both succeed and the same field is
    never silently overwritten (DRAFT_CONFLICT). One OPEN draft per person per file (IMPORT) or per paper version (CORRECTION)."""

    __tablename__ = "paper_review_drafts"
    __table_args__ = (
        CheckConstraint("kind IN ('IMPORT','CORRECTION')", name="paper_review_drafts_kind_check"),
        CheckConstraint("status IN ('OPEN','IMPORTED','DISCARDED')", name="paper_review_drafts_status_check"),
        CheckConstraint("(kind = 'IMPORT' AND file_id IS NOT NULL AND review_token IS NOT NULL AND file_ext IS NOT NULL) OR "
                        "(kind = 'CORRECTION' AND paper_version_id IS NOT NULL)", name="paper_review_drafts_kind_columns_check"),
        CheckConstraint("revision >= 1", name="paper_review_drafts_revision_check"),
        Index("ux_paper_review_drafts_open_import", "provider_id", "branch_id", "subject_id", "review_token", "created_by", unique=True,
              postgresql_where=_OPEN_IMPORT, sqlite_where=_OPEN_IMPORT),
        Index("ux_paper_review_drafts_open_correction", "provider_id", "branch_id", "subject_id", "paper_version_id", "created_by",
              unique=True, postgresql_where=_OPEN_CORRECTION, sqlite_where=_OPEN_CORRECTION),
        Index("ix_paper_review_drafts_branch_status", "branch_id", "status", "updated_at"),
    )

    draft_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.provider_id"), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.branch_id"), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    paper_id: Mapped[UUID | None] = mapped_column(ForeignKey("papers.paper_id"))                  # IMPORT as the next version of it
    paper_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("paper_versions.paper_version_id"))   # CORRECTION base
    title: Mapped[str | None] = mapped_column(String(255))
    file_id: Mapped[UUID | None] = mapped_column(ForeignKey("stored_files.file_id"))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_ext: Mapped[str | None] = mapped_column(String(10))
    answer_key_file_id: Mapped[UUID | None] = mapped_column(ForeignKey("stored_files.file_id"))
    review_token: Mapped[str | None] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)
    decisions: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="OPEN", server_default="OPEN")
    result_upload_id: Mapped[UUID | None] = mapped_column(ForeignKey("paper_uploads.upload_id"))
    result_paper_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("paper_versions.paper_version_id"))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    created_ip: Mapped[str | None] = mapped_column(IPAddress)
    updated_by: Mapped[UUID] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_ip: Mapped[str | None] = mapped_column(IPAddress)
    # when the review was last opened or resumed (not only changed): an abandoned review is one neither changed nor opened for the
    # retention period (services/draft_retention.py)
    last_opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
