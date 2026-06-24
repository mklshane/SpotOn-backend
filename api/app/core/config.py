"""Application configuration loaded from environment / .env via pydantic-settings."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database — Supabase SESSION pooler (postgresql+asyncpg://, port 5432).
    DATABASE_URL: str

    # Supabase Auth / API (new-style keys; this project uses asymmetric JWTs)
    SUPABASE_URL: str = ""
    SUPABASE_PUBLISHABLE_KEY: str = ""
    SUPABASE_SECRET_KEY: str = ""
    SUPABASE_JWKS_URL: str = ""
    # Fallback for HS256 projects (unused here, kept for portability)
    SUPABASE_JWT_SECRET: str = ""

    # App
    CORS_ORIGINS: str = "http://localhost:8081,http://localhost:19006"
    ENV: str = "dev"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @field_validator("DATABASE_URL")
    @classmethod
    def _check_async_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use the postgresql+asyncpg:// driver "
                "(Supabase session pooler, port 5432)."
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
