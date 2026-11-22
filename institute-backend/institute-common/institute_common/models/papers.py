from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, deferred, mapped_column
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

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

    `storage` names where the bytes live: "db" (the bytes are in `image_data`, BYTEA - every picture stored before object storage, and
    the existing upload path), or "local" / "s3" (an object store, institute_common.objectstore, at `storage_key`; `image_data` NULL).
    The question tables reference asset ids only, so where the bytes live never changes them. `image_data` is deferred: listing or
    loading questions never reads picture bytes. Bytes are served only by the authorised image endpoints."""

    __tablename__ = "question_image_assets"
    __table_args__ = (
        UniqueConstraint("provider_id", "checksum", name="uq_question_image_assets_provider_checksum"),
        # platform pictures (provider_id NULL, GLOBAL imports): one per checksum too (NULLs never collide in the constraint above)
        Index("uq_question_image_assets_platform_checksum", "checksum", unique=True,
              postgresql_where=text("provider_id IS NULL"), sqlite_where=text("provider_id IS NULL")),
        CheckConstraint("storage IN ('db','local','s3')", name="question_image_assets_storage_check"),
        # "db" pictures have no key; object-stored pictures always have one
        CheckConstraint("(storage = 'db') = (storage_key IS NULL)", name="question_image_assets_storage_key_check"),
        CheckConstraint("file_size > 0", name="question_image_assets_size_check"),
    )

    asset_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"), index=True)   # NULL = platform picture
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)                 # SHA-256 of the original bytes
    storage: Mapped[str] = mapped_column(String(10), nullable=False, default="db", server_default="db")
    image_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    storage_key: Mapped[str | None] = mapped_column(String(300))       # object key (never a public URL); NULL for "db"
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


class QuestionTaxonomyNode(Base):
    """One canonical Chapter, Topic or Subtopic of a provider's subject: Subject -> Chapter -> Topic -> Subtopic.

    A chapter has no parent; a topic's parent is a chapter and a subtopic's a topic (same provider and subject). Names are unique per
    parent, compared by `name_key` (core.taxonomy.label_key: case, spacing and Unicode width ignored), so labels that differ only in
    capitalisation or spacing are the same record. Records are never deleted: `status` RETIRED hides one from new classification
    while questions already linked to it keep the link; MERGED (`merged_into_id`) means a duplicate folded into another record (its
    questions were relinked there and its name became an alias of it). `provider_id` NULL: the PLATFORM list, used by platform
    (GLOBAL) questions (docs/institute-portal/TAXONOMY_ALIASES_PLAN.md)."""

    __tablename__ = "question_taxonomy_nodes"
    __table_args__ = (
        CheckConstraint("level IN ('CHAPTER','TOPIC','SUBTOPIC')", name="question_taxonomy_nodes_level_check"),
        CheckConstraint("(level = 'CHAPTER') = (parent_id IS NULL)", name="question_taxonomy_nodes_parent_check"),
        CheckConstraint("status IN ('ACTIVE','RETIRED','MERGED')", name="question_taxonomy_nodes_status_check"),
        CheckConstraint("(status = 'MERGED') = (merged_into_id IS NOT NULL)", name="question_taxonomy_nodes_merged_check"),
        # one name per place in the tree: chapters per (provider, subject) - or per subject on the platform list -, topics/subtopics
        # per parent
        Index("uq_question_taxonomy_chapter_name", "provider_id", "subject_id", "name_key", unique=True,
              postgresql_where=text("parent_id IS NULL"), sqlite_where=text("parent_id IS NULL")),
        Index("uq_question_taxonomy_platform_chapter_name", "subject_id", "name_key", unique=True,
              postgresql_where=text("parent_id IS NULL AND provider_id IS NULL"), sqlite_where=text("parent_id IS NULL AND provider_id IS NULL")),
        Index("uq_question_taxonomy_child_name", "parent_id", "name_key", unique=True,
              postgresql_where=text("parent_id IS NOT NULL"), sqlite_where=text("parent_id IS NOT NULL")),
        Index("ix_question_taxonomy_nodes_scope", "provider_id", "subject_id", "level"),
    )

    node_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"))          # NULL: the platform list
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"), index=True)
    merged_into_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"))
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)               # as shown (and copied into question labels)
    name_key: Mapped[str] = mapped_column(String(200), nullable=False)           # core.taxonomy.label_key(name)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    created_ip: Mapped[str | None] = mapped_column(IPAddress)
    updated_by: Mapped[UUID | None] = mapped_column()
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, onupdate=utcnow)


class QuestionTaxonomyAlias(Base):
    """Another spelling an authorised administrator approved for a taxonomy record ("Newton Laws" -> "Laws of Motion"). Future imports
    and question edits that use the alias are linked to the record. An alias is unique among the aliases of the same place in the tree
    (same provider, subject, level and parent); the service also refuses an alias that equals another record's name there."""

    __tablename__ = "question_taxonomy_aliases"
    __table_args__ = (
        Index("uq_question_taxonomy_chapter_alias", "provider_id", "subject_id", "alias_key", unique=True,
              postgresql_where=text("parent_id IS NULL"), sqlite_where=text("parent_id IS NULL")),
        Index("uq_question_taxonomy_platform_chapter_alias", "subject_id", "alias_key", unique=True,
              postgresql_where=text("parent_id IS NULL AND provider_id IS NULL"), sqlite_where=text("parent_id IS NULL AND provider_id IS NULL")),
        Index("uq_question_taxonomy_child_alias", "parent_id", "alias_key", unique=True,
              postgresql_where=text("parent_id IS NOT NULL"), sqlite_where=text("parent_id IS NOT NULL")),
    )

    alias_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"), nullable=False, index=True)
    # copied from the record so uniqueness can be enforced per place in the tree without a join (NULL: the platform list)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"))
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_key: Mapped[str] = mapped_column(String(200), nullable=False)          # core.taxonomy.label_key(alias)
    approved_by: Mapped[UUID] = mapped_column(nullable=False)
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    approved_ip: Mapped[str | None] = mapped_column(IPAddress)


class QuestionTaxonomyAliasRequest(Base):
    """"Remember this spelling" from someone who may not approve aliases (decision A2): a request for the record's administrators
    (the provider's Provider Admin; platform administrators for the platform list). Approving creates the alias; rejecting records why.
    The requester's own choice already applied where they made it (that upload or question) - the request is only about the future."""

    __tablename__ = "question_taxonomy_alias_requests"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="question_taxonomy_alias_requests_status_check"),
        CheckConstraint("source IN ('BULK_UPLOAD','QUESTION','CLASSIFY','UNLINKED')", name="question_taxonomy_alias_requests_source_check"),
        Index("ix_question_taxonomy_alias_requests_owner", "provider_id", "status", "requested_at"),
        Index("uq_question_taxonomy_alias_requests_pending", "node_id", "alias_key", unique=True,
              postgresql_where=text("status = 'PENDING'"), sqlite_where=text("status = 'PENDING'")),
    )

    request_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"), nullable=False)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.provider_id"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.subject_id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(64))                 # the upload / question it came from (shown to the approver)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING", server_default="PENDING")
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    decided_by: Mapped[UUID | None] = mapped_column()
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_reason: Mapped[str | None] = mapped_column(String(500))


class Question(Base):
    """Question-bank identity. Content lives in immutable question_versions.

    Named bank_questions because the Student test service already owns a table called `questions`
    (the questions of one delivered test) in the same shared database."""

    __tablename__ = "bank_questions"
    __table_args__ = (
        # GLOBAL questions belong to the platform catalogue (no provider/branch). PROVIDER questions belong to one provider and are
        # shared with ALL its branches: branch_id is the originating branch, NULL for a provider-wide question (services/bank_access.py).
        CheckConstraint("(scope = 'GLOBAL' AND provider_id IS NULL AND branch_id IS NULL) OR "
                        "(scope = 'PROVIDER' AND provider_id IS NOT NULL)", name="bank_questions_scope_check"),
        CheckConstraint(f"difficulty IS NULL OR difficulty IN ({sql_in(DIFFICULTIES)})", name="bank_questions_difficulty_check"),
        CheckConstraint(f"pattern IN ({sql_in(PATTERNS)})", name="bank_questions_pattern_check"),
        CheckConstraint(f"question_type IN ({sql_in(QUESTION_TYPES)})", name="bank_questions_type_check"),
        Index("ix_bank_questions_catalogue", "scope", "subject_id", "status"),
        Index("ix_bank_questions_taxonomy", "subject_id", "exam_type", "target_class", "chapter"),
        Index("ix_bank_questions_branch_id", "branch_id"),
        Index("ix_bank_questions_provider_subject", "provider_id", "subject_id", "status"),
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
    # Canonical taxonomy records of chapter/topic/subtopic (question_taxonomy_nodes). NULL while a label matches no record yet (or for
    # GLOBAL questions); the text labels above stay the readable copy every existing API returns.
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"))
    topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"))
    subtopic_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_taxonomy_nodes.node_id"))
    difficulty: Mapped[str | None] = mapped_column(String(10))
    pattern: Mapped[str] = mapped_column(String(30), nullable=False, default="SINGLE_CORRECT", server_default="SINGLE_CORRECT")
    subtype: Mapped[str | None] = mapped_column(String(80))
    question_type: Mapped[str] = mapped_column(String(20), nullable=False, default="MCQ_SINGLE", server_default="MCQ_SINGLE")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, onupdate=utcnow)


# Python None is SQL NULL here (not JSON 'null'), so the shape CHECK's `content IS NULL` holds for every MCQ.
NULLABLE_JSON = JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite")


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (
        UniqueConstraint("question_id", "version_no", name="uq_question_version"),
        # The shape follows the version's type (core/question_types.py): an MCQ keeps the columns it always had (four options, the
        # correct letter); an MSQ or NAT keeps its options / number format in `content` and its key in `answer_key`, its four
        # option columns empty and without pictures, and no correct letter.
        CheckConstraint("(question_type = 'MCQ_SINGLE' AND correct_option IS NOT NULL AND correct_option IN ('A','B','C','D') AND content IS NULL AND answer_key IS NULL) OR "
                        "(question_type IN ('MCQ_MULTI','NUMERICAL') AND correct_option IS NULL AND content IS NOT NULL AND "
                        "answer_key IS NOT NULL AND schema_version IS NOT NULL AND option_a = '' AND option_b = '' AND option_c = '' AND "
                        "option_d = '' AND option_a_image_id IS NULL AND option_b_image_id IS NULL AND option_c_image_id IS NULL AND "
                        "option_d_image_id IS NULL)", name="question_versions_shape_check"),
        # MSQ is all-or-nothing and never loses marks (R5): own marks, when given, have no negative part
        CheckConstraint("question_type <> 'MCQ_MULTI' OR marks_negative IS NULL OR marks_negative = 0",
                        name="question_versions_msq_negative_check"),
        CheckConstraint(f"question_type IN ({sql_in(QUESTION_TYPES)})", name="question_versions_type_check"),
        # Marks are optional (they belong to the test): a version has both marks or neither. Versions made before this rule all
        # have marks, which stay unchanged.
        # The IS NOT NULLs matter: a CHECK whose condition is NULL passes, so "marks_positive > 0" alone would let one mark be NULL.
        CheckConstraint("(marks_positive IS NULL AND marks_negative IS NULL) OR "
                        "(marks_positive IS NOT NULL AND marks_negative IS NOT NULL AND marks_positive > 0 AND marks_negative >= 0)",
                        name="question_versions_marks_check"),
    )

    question_version_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    question_id: Mapped[UUID] = mapped_column(ForeignKey("bank_questions.question_id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(Text, nullable=False)
    option_b: Mapped[str] = mapped_column(Text, nullable=False)
    option_c: Mapped[str] = mapped_column(Text, nullable=False)
    option_d: Mapped[str] = mapped_column(Text, nullable=False)
    correct_option: Mapped[str | None] = mapped_column(String(1))           # MCQ only; NULL for MSQ and NAT (their key: answer_key)
    # the type is copied onto every immutable version (decision D2) so the shape check above can see it; it never changes within a
    # question (the API refuses a version of another type)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False, default="MCQ_SINGLE", server_default="MCQ_SINGLE")
    content: Mapped[dict | None] = mapped_column(NULLABLE_JSON)          # MSQ options / NAT number format (shown to students)
    answer_key: Mapped[dict | None] = mapped_column(NULLABLE_JSON)       # MSQ correct set / NAT value + tolerance (never shown before review)
    schema_version: Mapped[int | None] = mapped_column(Integer)     # core/question_types.SCHEMA_VERSION of content + answer_key
    explanation: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(200))
    # NULL when the question carries no marks of its own; a test using RULES or UNIFORM marking then decides its marks.
    marks_positive: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    marks_negative: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    # pictures (question_image_assets); a field may have text, a picture or (the question) both. Text columns stay NOT NULL: a
    # picture-only option stores "" as its text.
    stem_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_a_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_b_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_c_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    option_d_image_id: Mapped[UUID | None] = mapped_column(ForeignKey("question_image_assets.asset_id"))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))


class QuestionVersionAsset(Base):
    """A picture referenced INSIDE a version's `content` (an MSQ option picture, roles option_a .. option_h). MCQ pictures keep their
    columns on question_versions. The link keeps the reference a real foreign key and lets the picture rules (who may see a
    picture) find every use of an asset."""
    __tablename__ = "question_version_assets"

    question_version_id: Mapped[UUID] = mapped_column(ForeignKey("question_versions.question_version_id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("question_image_assets.asset_id"), nullable=False, index=True)


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
    # Sum of its questions' own marks; NULL when any question has no marks (the test that uses the paper decides them).
    total_marks: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
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
