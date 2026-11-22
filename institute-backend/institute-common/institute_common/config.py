"""Settings shared by every Institute service. Each service reads only what it needs from its own environment
(see its .env.example); unused groups simply keep their defaults."""
from uuid import UUID

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICES = ("identity", "organization", "academics", "enrollment", "paper", "exam", "operations")
RECOGNITION_CATEGORIES = ("CHEMISTRY", "PHYSICS", "MATHEMATICS", "OTHER")      # OTHER: recorded decision, no subject conventions


class Settings(BaseSettings):
    app_env: str = "dev"
    service_name: str = "institute"
    database_url: str
    # a pooled connection idle longer than this is checked (one round trip) before reuse; recently used ones are reused directly
    db_ping_after_idle_seconds: int = 30
    redis_url: str = ""                      # only the identity service uses Redis (sessions, MFA, rate limits)
    cors_origins: str = ""
    frontend_base_url: str = "http://localhost:5174"

    session_cookie_secure: bool = True
    session_cookie_samesite: str = "strict"
    # DEVELOPMENT ONLY (refused for qa/prod): Chrome refuses a __Host- cookie on plain http://localhost (it keeps Secure cookies
    # there, but not the __Host- prefix), so requests whose Origin is http://localhost / 127.0.0.1 get the same Secure, HttpOnly,
    # SameSite cookie WITHOUT the prefix ("inst_sid"). Every other request - e.g. https through a tunnel - keeps __Host-.
    session_cookie_local_http: bool = False

    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 2
    hibp_enabled: bool = False

    email_backend: str = "console"
    email_from: str = "no-reply@example.invalid"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""

    # Inbound: keys that peer Institute services may present on /internal/v1 (comma-separated, two during rotation; empty = 404).
    internal_api_keys: str = ""
    # Outbound: one URL + key per peer Institute service. Keys never reach the browser.
    identity_service_url: str = ""
    identity_service_api_key: str = ""
    organization_service_url: str = ""
    organization_service_api_key: str = ""
    academics_service_url: str = ""
    academics_service_api_key: str = ""
    enrollment_service_url: str = ""
    enrollment_service_api_key: str = ""
    paper_service_url: str = ""
    paper_service_api_key: str = ""
    exam_service_url: str = ""
    exam_service_api_key: str = ""
    operations_service_url: str = ""
    operations_service_api_key: str = ""
    # Seconds between audit-spool forwarding runs inside a service process (0 = only when called explicitly, e.g. tests).
    audit_forward_interval_seconds: float = 5.0
    # Also try to deliver each audit event right after it is committed (the spool remains the fallback). Mainly for tests.
    audit_forward_inline: bool = False

    # Student services (existing, unchanged contracts).
    student_service_url: str = ""
    student_service_api_key: str = ""
    service_api_keys: str = ""
    student_result_service_url: str = ""
    student_result_service_api_key: str = ""
    student_attempt_service_url: str = ""
    student_attempt_service_api_key: str = ""
    student_notification_service_url: str = ""
    student_notification_service_api_key: str = ""
    student_test_service_url: str = ""
    student_test_service_api_key: str = ""
    # Question types exams may deliver to students (comma-separated: MCQ_SINGLE, MCQ_MULTI, NUMERICAL). MCQ only by default: MSQ / NAT
    # exams are enabled per environment only after the Student services that score them are deployed (QUESTION_TYPES_ARCHITECTURE.md).
    student_question_types: str = "MCQ_SINGLE"
    # Most questions one Global Test exam may hold (every paper of the date); never truncated: generation stops above it
    global_test_max_questions: int = 5000
    # Read projection of Institute-owned reference data (providers, branches, batches, enrollments) kept in the Student database
    # through the Student enrollment service's internal API. Pushed by the Institute enrollment service.
    student_enrollment_service_url: str = ""
    student_enrollment_service_api_key: str = ""
    reference_sync_interval_seconds: float = 15.0          # 0 = only on demand (tests)

    # Private file storage (paper service only).
    file_store_dir: str = "./var/files"
    # Object storage for question pictures (institute_common.objectstore). Credentials are never settings: the S3 client uses the
    # standard AWS credential chain (environment, credentials file or role).
    object_store: str = "local"                    # local | s3
    object_store_dir: str = "./var/objects"        # local only
    object_store_bucket: str = ""                  # s3 only; a PRIVATE bucket
    object_store_region: str = ""
    object_store_endpoint_url: str = ""            # optional S3-compatible endpoint (Cloudflare R2, MinIO); empty for AWS S3
    object_store_prefix: str = ""                  # optional key prefix, e.g. "prod/"
    object_store_signed_url_seconds: int = 300     # lifetime of a signed download URL
    # Bulk question import (paper service + bulk import worker)
    bulk_import_max_bytes: int = 2 * 1024 ** 3         # largest uploaded .xlsx/.docx, pictures included (2 GiB; streamed, never in memory)
    bulk_import_max_rows: int = 100_000                # questions per Excel file
    bulk_import_max_docx_rows: int = 5_000             # questions per Word file (Word is meant for smaller collections)
    bulk_import_max_active_jobs: int = 3               # unfinished imports one provider may have at a time
    bulk_import_batch_size: int = 200                  # questions per database transaction while importing
    bulk_import_lease_seconds: int = 300               # a worker that stops renewing its lease for this long is presumed dead
    bulk_import_max_attempts: int = 3                  # claims before a job that keeps failing is stopped
    bulk_import_preview_days: int = 7                  # an unconfirmed preview is discarded (and its files deleted) after this
    bulk_upload_chunk_bytes: int = 8 * 1024 * 1024     # resumable uploads: the size of each piece the browser sends
    bulk_upload_resume_days: int = 3                   # an unfinished resumable upload can be continued this long, then it is deleted
    max_upload_bytes: int = 2 * 1024 * 1024
    # .xlsx workbooks and .zip packages (CSV + images) carry question pictures: each picture is at most 5 MB (question_images.py)
    max_package_upload_bytes: int = 25 * 1024 * 1024
    max_paper_questions: int = 500
    # Uploaded papers (CSV/XLSX/DOCX/PDF) are read in an isolated process (paper service): wall-clock limit, and memory allowed on top
    # of what the worker already holds. A 500-question paper takes well under 2 s and a few MiB.
    extraction_isolated: bool = True
    extraction_timeout_seconds: float = 20.0
    extraction_memory_mb: int = 512
    # Recognition of uploaded papers (paper service): the subject category of each authoritative subject_id, as a JSON object
    # {"<subject uuid>": "CHEMISTRY" | "PHYSICS" | "MATHEMATICS" | "OTHER"}. Only the first three get subject-specific recognition (Chemistry's
    # molar M, chemistry formula conventions, Mathematics' matrix powers); every other subject - including one whose NAME mentions a
    # science - is recognised without subject conventions, so its doubtful cases go to provider review. Subject names are never parsed.
    recognition_subject_categories: dict[str, str] = {}

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("recognition_subject_categories")
    @classmethod
    def _subject_categories(cls, value: dict[str, str]) -> dict[str, str]:
        out = {}
        for key, category in value.items():
            try:
                subject_id = str(UUID(str(key)))
            except ValueError:
                raise ValueError(f"RECOGNITION_SUBJECT_CATEGORIES: {key!r} is not a subject id (UUID)") from None
            if str(category).upper() not in RECOGNITION_CATEGORIES:
                raise ValueError(f"RECOGNITION_SUBJECT_CATEGORIES: {category!r} is not one of {', '.join(RECOGNITION_CATEGORIES)}")
            out[subject_id] = str(category).upper()
        return out

    @model_validator(mode="after")
    def validate_environment(self):
        if self.session_cookie_samesite.lower() not in {"strict", "lax", "none"}:
            raise ValueError("SESSION_COOKIE_SAMESITE must be strict, lax or none")
        if self.session_cookie_samesite.lower() == "none" and not self.session_cookie_secure:
            raise ValueError("SameSite=None requires secure cookies")
        if "*" in self.origins:
            raise ValueError("CORS_ORIGINS must be an explicit allowlist (no wildcard)")
        if self.app_env in {"qa", "prod"}:
            problems = []
            if not self.session_cookie_secure:
                problems.append("SESSION_COOKIE_SECURE must be true")
            if self.session_cookie_local_http:
                problems.append("SESSION_COOKIE_LOCAL_HTTP is dev-only")
            if self.email_backend == "console":
                problems.append("EMAIL_BACKEND=console is dev-only")
            if any(o.startswith("http://") for o in self.origins):
                problems.append("CORS origins must be https")
            if problems:
                raise ValueError("; ".join(problems))
        return self


settings = Settings()
