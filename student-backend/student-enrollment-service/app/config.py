from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    # connection pool per process (ignored for sqlite): a request waits at most db_pool_timeout seconds for a free connection
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout: int = 10
    cors_origins: str
    redis_url: str
    # Comma-separated service keys the Institute may present on /internal/v1 (empty = internal API disabled; two during rotation).
    institute_service_api_keys: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()