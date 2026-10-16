from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    database_url: str
    cors_origins: str
    session_cookie_secure: bool
    redis_url: str
    resend_api_key: str = ""
    email_from: str = ""
    frontend_reset_url: str
    # "resend" sends through Resend (RESEND_API_KEY + EMAIL_FROM). "console" writes the message to the service log instead and
    # is accepted only when APP_ENV=dev (local stacks without an email provider). Empty = resend when configured, else nothing.
    email_backend: str = ""
    # Comma-separated service keys allowed to call /internal/v1 (empty = internal API disabled).
    # Two keys may be listed during rotation. Used by the Institute backend.
    institute_service_api_keys: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


    @model_validator(mode="after")
    def _email_backend_allowed(self):
        if self.email_backend not in {"", "resend", "console"}:
            raise ValueError("EMAIL_BACKEND must be resend or console")
        if self.email_backend == "console" and self.app_env != "dev":
            raise ValueError("EMAIL_BACKEND=console is only allowed with APP_ENV=dev")
        if self.email_backend == "resend" and not (self.resend_api_key and self.email_from):
            raise ValueError("EMAIL_BACKEND=resend needs RESEND_API_KEY and EMAIL_FROM")
        return self


settings = Settings()
