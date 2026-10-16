from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379"
    cors_origins: str = ""
    # Comma-separated service keys for /internal/v1 (empty = disabled; two during rotation). Used by the Institute backend.
    institute_service_api_keys: str = ""
    # Legacy POST /notifications requires a configured X-Service-Key.
    # Empty = endpoint disabled with HTTP 503.
    notification_create_api_keys: str = ""

    class Config:
        env_file = ".env"


settings = Settings()