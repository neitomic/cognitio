"""Typed application settings for Cognitio.

The composition roots (``cognitio_api`` and ``cognitio_worker``) construct :class:`Settings`
instead of reading environment variables directly. Values come from (highest priority first):

1. explicit keyword arguments to ``Settings(...)`` (used by tests),
2. process environment variables,
3. a local ``.env`` file (see ``.env.example``).

Secrets are wrapped in :class:`pydantic.SecretStr` so they never leak through ``repr`` or logs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Connection-string schemes we accept for the async SQLAlchemy engine.
_POSTGRES_SCHEMES = (
    "postgresql+asyncpg://",
    "postgresql://",
    "postgres://",
)


def _split_csv(value: object) -> object:
    """Allow list-typed settings to be provided as a comma-separated string in env vars.

    ``FALLBACK_ACL_PRINCIPALS=alice,bob`` becomes ``("alice", "bob")``. Real lists (e.g. from
    keyword arguments) and JSON arrays pass through untouched.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        if stripped.startswith("["):  # let pydantic parse JSON arrays itself
            return value
        return tuple(part.strip() for part in stripped.split(",") if part.strip())
    return value


CsvTuple = Annotated[tuple[str, ...], BeforeValidator(_split_csv)]


def _validate_postgres_url(value: str, *, field: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field} must not be empty")
    if not value.startswith(_POSTGRES_SCHEMES):
        raise ValueError(
            f"{field} must be a PostgreSQL connection URL starting with one of "
            f"{', '.join(_POSTGRES_SCHEMES)} (got {value.split('://', 1)[0]!r})"
        )
    return value


class Settings(BaseSettings):
    """Validated, environment-backed application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database --------------------------------------------------------------------------
    database_url: str = Field(alias="DATABASE_URL")
    test_database_url: str | None = Field(default=None, alias="TEST_DATABASE_URL")

    # --- Notion connector ------------------------------------------------------------------
    notion_token: SecretStr | None = Field(default=None, alias="NOTION_TOKEN")
    notion_root_ids: CsvTuple = Field(default=(), alias="NOTION_ROOT_IDS")

    # --- Anthropic / extraction ------------------------------------------------------------
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL")

    # --- Embeddings ------------------------------------------------------------------------
    embedding_provider: str = Field(default="openai", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_model_version: str = Field(
        default="text-embedding-3-small/1", alias="EMBEDDING_MODEL_VERSION"
    )
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")

    # --- Fallback ACL ----------------------------------------------------------------------
    # Principals granted access when a source exposes no usable permission metadata.
    fallback_acl_principals: CsvTuple = Field(default=(), alias="FALLBACK_ACL_PRINCIPALS")

    # --- Worker timing ---------------------------------------------------------------------
    worker_poll_interval_seconds: float = Field(
        default=1.0, gt=0, alias="WORKER_POLL_INTERVAL_SECONDS"
    )
    worker_claim_batch: int = Field(default=10, ge=1, alias="WORKER_CLAIM_BATCH")
    worker_stale_lock_seconds: float = Field(default=300.0, gt=0, alias="WORKER_STALE_LOCK_SECONDS")
    worker_max_attempts: int = Field(default=5, ge=1, alias="WORKER_MAX_ATTEMPTS")

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        return _validate_postgres_url(value, field="DATABASE_URL")

    @field_validator("test_database_url")
    @classmethod
    def _check_test_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_postgres_url(value, field="TEST_DATABASE_URL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, constructed once from the environment/.env file."""
    return Settings()  # type: ignore[call-arg]  # values are supplied by env/.env
