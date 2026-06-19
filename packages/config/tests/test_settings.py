"""Unit tests for cognitio_config.Settings."""

from __future__ import annotations

import pytest
from cognitio_config import Settings, get_settings
from pydantic import SecretStr, ValidationError

_VALID_DB = "postgresql+asyncpg://cognitio:cognitio@localhost:5432/cognitio"


def _settings(**env: str) -> Settings:
    """Build Settings from explicit values, ignoring any local .env file."""
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_database_url_is_required() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings()
    assert "DATABASE_URL" in str(excinfo.value)


def test_minimal_settings_have_documented_defaults() -> None:
    settings = _settings(DATABASE_URL=_VALID_DB)

    assert settings.database_url == _VALID_DB
    assert settings.test_database_url is None
    assert settings.anthropic_model == "claude-sonnet-4-6"
    assert settings.embedding_provider == "openai"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_model_version == "text-embedding-3-small/1"
    assert settings.fallback_acl_principals == ()
    assert settings.notion_root_ids == ()
    assert settings.worker_poll_interval_seconds == 1.0
    assert settings.worker_claim_batch == 10
    assert settings.worker_stale_lock_seconds == 300.0
    assert settings.worker_max_attempts == 5
    # Unset secrets default to None, not empty SecretStr.
    assert settings.notion_token is None
    assert settings.anthropic_api_key is None


def test_secrets_are_wrapped_and_redacted() -> None:
    settings = _settings(
        DATABASE_URL=_VALID_DB,
        NOTION_TOKEN="ntn_supersecret",
        ANTHROPIC_API_KEY="sk-ant-supersecret",
        OPENAI_API_KEY="sk-openai-supersecret",
    )

    assert isinstance(settings.notion_token, SecretStr)
    assert isinstance(settings.anthropic_api_key, SecretStr)

    # The plaintext must never appear in repr/str output.
    rendered = repr(settings) + str(settings)
    assert "supersecret" not in rendered
    assert "ntn_supersecret" not in rendered

    # ...but is recoverable on purpose.
    assert settings.notion_token.get_secret_value() == "ntn_supersecret"
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-supersecret"


@pytest.mark.parametrize(
    "bad_url",
    [
        "mysql://user:pass@localhost/db",
        "http://localhost:5432/cognitio",
        "localhost:5432/cognitio",
        "",
    ],
)
def test_invalid_database_url_is_rejected(bad_url: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(DATABASE_URL=bad_url)
    assert "DATABASE_URL" in str(excinfo.value)


def test_invalid_test_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(DATABASE_URL=_VALID_DB, TEST_DATABASE_URL="redis://localhost:6379")
    assert "TEST_DATABASE_URL" in str(excinfo.value)


def test_plain_postgresql_scheme_is_accepted() -> None:
    settings = _settings(DATABASE_URL="postgresql://u:p@localhost:5432/db")
    assert settings.database_url.startswith("postgresql://")


def test_comma_separated_lists_are_parsed() -> None:
    settings = _settings(
        DATABASE_URL=_VALID_DB,
        NOTION_ROOT_IDS="root-a, root-b ,root-c",
        FALLBACK_ACL_PRINCIPALS="alice,bob",
    )
    assert settings.notion_root_ids == ("root-a", "root-b", "root-c")
    assert settings.fallback_acl_principals == ("alice", "bob")


def test_worker_timing_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        _settings(DATABASE_URL=_VALID_DB, WORKER_CLAIM_BATCH="0")
    with pytest.raises(ValidationError):
        _settings(DATABASE_URL=_VALID_DB, WORKER_POLL_INTERVAL_SECONDS="0")


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composition roots construct Settings() with no args; values come from the environment."""
    monkeypatch.setenv("DATABASE_URL", _VALID_DB)
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.database_url == _VALID_DB
    assert settings.anthropic_model == "claude-haiku-4-5-20251001"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _VALID_DB)
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
