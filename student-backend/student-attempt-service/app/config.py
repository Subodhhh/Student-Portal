from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    cors_origins: str = ""
    # Comma-separated service keys for /internal/v1 (empty = disabled; two during rotation). Used by the Institute backend.
    institute_service_api_keys: str = ""
    # Exam integrity: counted browser warnings (tab hidden, window blur, fullscreen exit) that automatically submit an attempt, and
    # the window in which several browser events of one action count as a single warning.
    integrity_warning_threshold: int = 3
    integrity_debounce_seconds: float = 2.0
    # Global Test with paper choice: the test service's internal API, read for a student's eligible papers (immutable after delivery).
    # Unset = paper selection unavailable (503) for tests with paper choice only; every other test never calls it.
    student_test_service_url: str = ""
    student_test_service_api_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()