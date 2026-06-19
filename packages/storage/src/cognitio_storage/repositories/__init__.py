"""Typed, tenant-scoped repository functions — the only write/read path above Layer 1.

Every repository takes an :class:`~sqlalchemy.ext.asyncio.AsyncSession` (supplied by the
caller's :class:`cognitio_storage.db.Uow`) and scopes every statement by ``tenant_id``. No
layer above Storage issues raw SQL.
"""

from cognitio_storage.repositories.edges import EdgeCapExceeded, EdgeRepository
from cognitio_storage.repositories.extractions import ExtractionRepository
from cognitio_storage.repositories.normalization import (
    ChunkInput,
    NormalizedChunkRepository,
    NormalizedDocumentRepository,
)
from cognitio_storage.repositories.source import (
    ChangeEventRepository,
    SourceItemRepository,
    SourceVersionRepository,
)
from cognitio_storage.repositories.sync import (
    ConnectorScanRepository,
    ConnectorSyncStateRepository,
)
from cognitio_storage.repositories.tenancy import (
    EmbeddingRepository,
    EntityRepository,
    PrincipalRepository,
    ReviewItemRepository,
    TenantRepository,
)

__all__ = [
    "ChangeEventRepository",
    "ChunkInput",
    "ConnectorScanRepository",
    "ConnectorSyncStateRepository",
    "EdgeCapExceeded",
    "EdgeRepository",
    "EmbeddingRepository",
    "EntityRepository",
    "ExtractionRepository",
    "NormalizedChunkRepository",
    "NormalizedDocumentRepository",
    "PrincipalRepository",
    "ReviewItemRepository",
    "SourceItemRepository",
    "SourceVersionRepository",
    "TenantRepository",
]
