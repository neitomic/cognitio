# 0004. ACL on derived records is the union of source denies

- Status: Accepted
- Date: 2026-06-17

## Context

For a company knowledge platform, leaking a fact a user could not see at the source is a
**showstopper**. The danger is subtle: a private fact, once extracted, can flow into a derived record
or a cross-source synthesis and be surfaced to someone who never had access to the original.

A derived `extraction` typically draws on one source; a synthesized record draws on several. The
question is: who may see a derived record built from multiple sources with different access lists?

Two further realities complicate any captured-snapshot scheme:

- **Permission changes are content-invisible** — an object's ACL can change without its content
  changing, so they must be treated as changes that re-capture the ACL.
- **Most ACLs are expressed via groups**, whose membership changes in the IdP/workspace, *not* on the
  object — so a captured, expanded member list goes stale and either leaks to removed members or
  blocks newly-added ones.

## Decision

Adopt **most-restrictive-wins**, framed consistently as the **union of source denies** (equivalently,
the intersection of source allow-sets): a derived record is visible only to principals who could see
**all** of its sources. A synthesized record carries the intersection of its constituents' viewer
sets.

Mechanically:

1. **Ingest ACLs at fetch time** into `source_versions.acl_snapshot`; a permission change triggers a
   re-fetch even when content is unchanged.
2. **Propagate restrictions through derivation:** every `extraction.effective_acl` is the union of its
   source denies.
3. **Resolve group membership live at query time** (short-TTL cache in `principals`), never from a
   captured expanded list.
4. **Enforce before ranking** and **before any content reaches a prompt** — search and traversal
   filter candidates by the requesting principal's resolved permissions first.
5. **Audit** promotion actions and query access per principal.

Phase 1 (single connector, single tenant) may use captured principal lists directly; cross-source
identity mapping and live group resolution harden with the second connector in Phase 2.

## Consequences

- **Easier:** a single, conservative rule covers derivation and synthesis; "can this principal see
  this record?" is a deterministic set operation; live group resolution closes the add/remove leak
  window.
- **Harder / accepted (deliberate over-restriction):** a fact that *also* appears in a public document
  is hidden from people who can only see the public source, because the derived record inherits the
  *union* of denies. This is a safe default, not an oversight. An opt-in override path (shareable /
  public Gold) is deferred to Phase 4 and must not reopen leak risk.
- **Harder / accepted:** live group resolution adds latency to every query; the membership-cache TTL
  trades leak-window against latency and must be tuned (an open question in DESIGN).
- **Guardrail:** never let an API route bypass Query-Layer ACL filtering. The API resolves *who* asks;
  the Query Layer decides *what they may see*.
