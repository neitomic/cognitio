"""Reusable column types, the declarative ``Base``, and tenant-safe FK helpers.

Every table in Cognitio shares three columns — a UUID primary key, a non-null
``tenant_id``, and a ``created_at`` timestamp — and most foreign keys must stay *within a
single tenant*. The helpers here centralise those conventions so ``models.py`` stays
declarative and the tenant invariant is enforced by the database, not by hope.

Tenant-safe foreign keys
------------------------
A plain ``ForeignKey("parent.id")`` lets a child in tenant A point at a parent row in
tenant B. To make that impossible the parent exposes a composite unique key
``(tenant_id, id)`` (see :func:`tenant_unique`) and the child references it with a
*composite* foreign key on ``(tenant_id, <col>)`` (see :func:`tenant_fk`). Postgres then
rejects any cross-tenant reference structurally.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, ForeignKeyConstraint, MetaData, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint/index names so Alembic autogenerate and migrations stay stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base carrying the shared metadata + naming convention."""

    metadata = _metadata


# --- Reusable annotated column types ---------------------------------------------------------
# PEP 593 ``Annotated`` aliases; SQLAlchemy clones the embedded ``mapped_column`` per use.
UuidPk = Annotated[
    uuid.UUID,
    mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()),
]
TenantId = Annotated[uuid.UUID, mapped_column(PGUUID(as_uuid=True), nullable=False)]
CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now()),
]


class TenantScoped(Base):
    """Abstract base: id PK, non-null ``tenant_id``, ``created_at``.

    Every concrete table inherits these. ``tenant_id`` is a mandatory predicate on every
    query (AGENTS.md → "Every row carries ``tenant_id``").
    """

    __abstract__ = True

    id: Mapped[UuidPk]
    tenant_id: Mapped[TenantId]
    created_at: Mapped[CreatedAt]


def updated_at_column() -> Mapped[datetime]:
    """A ``updated_at`` column defaulting to ``now()`` on insert and update."""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def fk_uuid(*, nullable: bool = False) -> Mapped[uuid.UUID]:
    """A UUID column intended as the local side of a (composite) foreign key."""
    return mapped_column(PGUUID(as_uuid=True), nullable=nullable)


def optional_fk_uuid() -> Mapped[uuid.UUID | None]:
    """A nullable UUID foreign-key column (set after a later pass, e.g. resolution)."""
    return mapped_column(PGUUID(as_uuid=True), nullable=True)


def tenant_unique() -> UniqueConstraint:
    """Composite unique ``(tenant_id, id)`` so a row is a valid tenant-safe FK target."""
    return UniqueConstraint("tenant_id", "id")


def tenant_fk(
    column: str,
    target_table: str,
    *,
    ondelete: str | None = None,
) -> ForeignKeyConstraint:
    """A tenant-scoped composite FK: ``(tenant_id, column) -> (target.tenant_id, target.id)``.

    Guarantees the referenced parent shares this row's ``tenant_id``; cross-tenant references
    are rejected by Postgres. The parent table must declare :func:`tenant_unique`.
    """
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target_table}.tenant_id", f"{target_table}.id"],
        ondelete=ondelete,
    )
