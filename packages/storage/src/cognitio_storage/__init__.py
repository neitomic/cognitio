"""cognitio-storage — Layer 1.

Postgres schema, migrations, and typed repositories. The bottom layer: every other
layer reaches durable state only through the repositories and the unit-of-work
(`Uow`) exposed here.

Public surface:
    - ``enums``         : the Postgres ENUM value sets, mirrored as Python enums.

Phase 1 (task 6+) adds the remaining surface — ``models`` (SQLAlchemy models,
one per table), ``db`` (engine, session factory, ``Uow``), and ``repositories``
(typed repository functions per table). They are intentionally absent from the
Phase 0 skeleton so the package imports cleanly before those modules exist.
"""

from cognitio_storage import enums

__all__ = ["enums"]
