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

    # Custom auth — we issue our own HS256 JWTs (Supabase is DB-only now).
    JWT_SECRET: str = ""
    ACCESS_TOKEN_TTL_MIN: int = 60
    REFRESH_TOKEN_TTL_DAYS: int = 60

    # Supabase (DB connection only; the auth keys below are unused / legacy).
    SUPABASE_URL: str = ""
    SUPABASE_PUBLISHABLE_KEY: str = ""
    SUPABASE_SECRET_KEY: str = ""
    SUPABASE_JWKS_URL: str = ""
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
