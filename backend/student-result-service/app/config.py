from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379"
    cors_origins: str = ""
    # Comma-separated service keys for /internal/v1 (empty = disabled; two during rotation).
    institute_service_api_keys: str = ""

    class Config:
        env_file = ".env"


settings = Settings()