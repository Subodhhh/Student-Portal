from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379"
    cors_origins: str = ""
    # Comma-separated service keys for /internal/v1 (empty = internal API disabled; two during rotation).
    institute_service_api_keys: str = ""
    # Most questions one Global Test exam with paper choice may deliver; never truncated, an exam above it is rejected.
    max_exam_questions: int = 5000

    class Config:
        env_file = ".env"


settings = Settings()