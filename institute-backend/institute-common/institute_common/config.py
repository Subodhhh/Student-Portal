"""Settings shared by every Institute service. Each service reads only what it needs from its own environment
(see its .env.example); unused groups simply keep their defaults."""
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICES = ("identity", "organization", "academics", "enrollment", "paper", "exam", "operations")


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
    # Read projection of Institute-owned reference data (providers, branches, batches, enrollments) kept in the Student database
    # through the Student enrollment service's internal API. Pushed by the Institute enrollment service.
    student_enrollment_service_url: str = ""
    student_enrollment_service_api_key: str = ""
    reference_sync_interval_seconds: float = 15.0          # 0 = only on demand (tests)

    # Private file storage (paper service only).
    file_store_dir: str = "./var/files"
    max_upload_bytes: int = 2 * 1024 * 1024
    max_paper_questions: int = 500
    ocr_min_confidence: float = 0.98
    # "" = no OCR (image papers are rejected). "tesseract" = printed-text OCR in the paper service image; its confidences are not
    # calibrated probabilities, so set OCR_MIN_CONFIDENCE deliberately (every OCR draft still needs human preview and approval).
    ocr_engine: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

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
            if self.email_backend == "console":
                problems.append("EMAIL_BACKEND=console is dev-only")
            if any(o.startswith("http://") for o in self.origins):
                problems.append("CORS origins must be https")
            if problems:
                raise ValueError("; ".join(problems))
        return self


settings = Settings()
