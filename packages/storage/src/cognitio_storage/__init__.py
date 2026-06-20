"""cognitio-storage — Layer 1.

Postgres schema, migrations, and typed repositories. The bottom layer: every other
layer reaches durable state only through the repositories and the unit-of-work
(`Uow`) exposed here.

Public surface:
    - ``enums``         : the Postgres ENUM value sets, mirrored as Python enums.
    - ``types``         : the declarative ``Base``, shared column types, tenant-safe FK helpers.
    - ``models``        : SQLAlchemy models, one per table.
    - ``db``            : async engine / session factory and the ``Uow`` primitive.
    - ``repositories``  : typed, tenant-scoped repository functions per table.
"""

from cognitio_storage import db, enums, models, repositories, types
from cognitio_storage.db import Uow, create_engine, create_session_factory
from cognitio_storage.types import Base

__all__ = [
    "Base",
    "Uow",
    "create_engine",
    "create_session_factory",
    "db",
    "enums",
    "models",
    "repositories",
    "types",
]
