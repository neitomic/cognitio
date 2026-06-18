"""Most-restrictive-wins ACL resolution and candidate visibility checks."""

from __future__ import annotations

from typing import Protocol

from cognitio_query.types import EffectiveAcl, Principal, ResolvedAcl


class GroupMembershipProvider(Protocol):
    async def resolve(self, principal: Principal) -> frozenset[str]: ...


class AclResolver:
    def __init__(self, groups: GroupMembershipProvider | None = None) -> None:
        self._groups = groups

    async def effective_principals(self, principal: Principal) -> ResolvedAcl:
        live_groups = (
            await self._groups.resolve(principal)
            if self._groups is not None
            else principal.group_ids
        )
        identities = frozenset({str(principal.id), *principal.source_identities.values()})
        return ResolvedAcl(principal_ids=identities, group_ids=live_groups)

    def allows(self, acl: EffectiveAcl, resolved: ResolvedAcl) -> bool:
        if resolved.principal_ids & acl.denied_principals:
            return False
        if resolved.group_ids & acl.denied_groups:
            return False
        if acl.public:
            return True
        return bool(
            resolved.principal_ids & acl.allowed_principals
            or resolved.group_ids & acl.allowed_groups
        )
