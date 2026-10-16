from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    database_url: str
    redis_url: str
    cors_origins: str
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

    # Private file storage (local disk now; an object-storage provider is not chosen yet).
    file_store_dir: str = "./var/files"
    max_upload_bytes: int = 2 * 1024 * 1024
    max_paper_questions: int = 500
    # Image papers are accepted only if an OCR backend is registered in code; every question must reach this confidence or the whole paper is rejected.
    ocr_min_confidence: float = 0.98

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
