# Cognitio task runner. Run `just` to list recipes.
# Requires: just (https://just.systems), uv, and Docker for the db recipes.

# Connection params mirror compose.yaml.
pg_user := "cognitio"
dev_db := "cognitio"

# Show available recipes.
default:
    @just --list

# --- Infrastructure ----------------------------------------------------------------------
# Start local Postgres (pgvector) in the background.
up:
    docker compose up -d

# Stop local Postgres (keeps the data volume).
down:
    docker compose down

# Drop and recreate the development database, then re-create required extensions.
reset-db:
    docker compose exec -T postgres psql -U {{pg_user}} -d postgres \
        -c "DROP DATABASE IF EXISTS {{dev_db}} WITH (FORCE);"
    docker compose exec -T postgres psql -U {{pg_user}} -d postgres \
        -c "CREATE DATABASE {{dev_db}} OWNER {{pg_user}};"
    docker compose exec -T postgres psql -U {{pg_user}} -d {{dev_db}} \
        -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;"

# --- Workspace ---------------------------------------------------------------------------
# Install/refresh the uv workspace.
sync:
    uv sync

# --- Quality gates -----------------------------------------------------------------------
# Lint + verify formatting (no changes).
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-format the codebase.
fmt:
    uv run ruff format .

# Type-check shipped source (mypy --strict).
type:
    uv run mypy

# Fast unit tests (no Docker, no credentials).
test:
    uv run pytest -m "unit"

# Integration tests (require Postgres via TEST_DATABASE_URL).
test-int:
    uv run pytest -m "integration"

# Integration tests, but only when TEST_DATABASE_URL is set (no-op otherwise).
# Lets `just ci` reproduce CI locally without forcing a Postgres on every run.
ci-int:
    #!/usr/bin/env sh
    if [ -n "$TEST_DATABASE_URL" ]; then
        uv run pytest -m "integration"
    else
        echo "TEST_DATABASE_URL not set — skipping integration tests (they run in CI)."
    fi

# Everything CI runs, in order (integration runs only when TEST_DATABASE_URL is set).
ci: lint type test ci-int
