#!/usr/bin/env bash
# Enable required extensions in BOTH the dev and test databases.
# pgvector ships the `vector` extension; `pgcrypto` provides the crypto-shred primitives
# ARCHITECTURE requires from day 1.
set -euo pipefail

for db in "${POSTGRES_DB}" cognitio_test; do
  echo "Creating extensions in database: ${db}"
  psql --variable=ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${db}" <<-'SQL'
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
SQL
done
