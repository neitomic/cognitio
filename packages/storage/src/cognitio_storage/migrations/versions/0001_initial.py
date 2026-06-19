"""initial schema — all Phase 1 tables, enums, indexes, fan-out trigger

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-20

The whole Phase-1 schema is created from the SQLAlchemy metadata (tables, enums, unique
constraints, partial ``is_current`` indexes, GIN/functional indexes, tenant-safe composite
FKs). Three things the ORM metadata cannot express are added explicitly afterwards:

* the ``vector`` / ``pgcrypto`` extensions (created first — the ``embeddings.vector`` column
  needs the ``vector`` type to exist),
* the per-``model_version`` HNSW index for ANN search, and
* the edge fan-out cap trigger (``supports`` ≤ 50, ``contradicts`` ≤ 20 per source node).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from cognitio_storage.types import Base

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Phase-1 active embedding model version (mirrors Settings.embedding_model_version default).
# The HNSW index is pinned per version: one space per index (cosine distance is meaningless
# across versions). Re-embeds are blue/green — add a new partial index for the new version.
ACTIVE_MODEL_VERSION = "text-embedding-3-small/1"

_FANOUT_TRIGGER = """
CREATE OR REPLACE FUNCTION enforce_edge_fanout() RETURNS trigger AS $$
DECLARE
    cnt integer;
BEGIN
    IF NEW.type = 'supports' THEN
        SELECT count(*) INTO cnt FROM edges
            WHERE tenant_id = NEW.tenant_id AND from_id = NEW.from_id AND type = 'supports';
        IF cnt >= 50 THEN
            RAISE EXCEPTION 'supports fan-out cap (50) exceeded for node %', NEW.from_id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.type = 'contradicts' THEN
        SELECT count(*) INTO cnt FROM edges
            WHERE tenant_id = NEW.tenant_id AND from_id = NEW.from_id AND type = 'contradicts';
        IF cnt >= 20 THEN
            RAISE EXCEPTION 'contradicts fan-out cap (20) exceeded for node %', NEW.from_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Tables + enums + constraints + partial/functional indexes, straight from the models.
    Base.metadata.create_all(bind)

    # Per-version HNSW ANN index (pgvector opclass — not expressible in ORM metadata).
    op.execute(
        "CREATE INDEX ix_emb_hnsw_active ON embeddings USING hnsw (vector vector_cosine_ops) "
        f"WHERE model_version = '{ACTIVE_MODEL_VERSION}'"
    )

    # Edge fan-out caps enforced at write time.
    op.execute(_FANOUT_TRIGGER)
    op.execute(
        "CREATE TRIGGER edge_fanout_cap BEFORE INSERT ON edges "
        "FOR EACH ROW EXECUTE FUNCTION enforce_edge_fanout()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TRIGGER IF EXISTS edge_fanout_cap ON edges")
    op.execute("DROP FUNCTION IF EXISTS enforce_edge_fanout()")
    op.execute("DROP INDEX IF EXISTS ix_emb_hnsw_active")
    Base.metadata.drop_all(bind)
