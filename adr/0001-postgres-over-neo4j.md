# 0001. PostgreSQL (+ pgvector) over a dedicated graph DB

- Status: Accepted
- Date: 2026-06-17

## Context

Cognitio is, conceptually, a knowledge **graph**: typed nodes (decisions, actions, facts, entities)
connected by typed edges (`derived_from`, `supersedes`, `supports`, `contradicts`). The obvious
reflex is to reach for a graph-native database (Neo4j) from day 1.

But Phase 1 needs four things at once that are *not* primarily graph operations:

- a **transactional store** for typed, versioned records with strong constraints;
- a **job queue** with retry/backoff/dead-letter for the incremental cascade;
- **review/workflow state** with an audit trail;
- a **vector index** for semantic search.

Running these across a graph DB *plus* a queue *plus* a vector store multiplies operational surface
and introduces cross-store consistency problems (a job commits in one store, the derived row in
another) precisely while we are still trying to *prove* the product earns trust. The actual graph
traversals in Phase 1 are shallow (1–3 hops) and easily expressed as recursive CTEs over an
adjacency-list `edges` table.

## Decision

Use **PostgreSQL (≥ 16) with the `pgvector` and `pgcrypto` extensions** as the single store for
Phase 1: typed tables for every tier, an adjacency-list `edges` table, the `SKIP LOCKED` job queue,
review state, cost accounting, and the HNSW vector index — all in one transactional database.

Graph traversal is done with recursive CTEs and per-edge-type fan-out caps, not a graph engine.

The migration trigger to a graph DB (Neo4j) is defined **concretely**, not as "if traversal
matters": move when graph-native operations become central UX — sustained deep multi-hop (>2–3 hops)
at low latency, path queries ("the chain of decisions that led to this policy"), graph algorithms
(centrality/community/propagation), interactive neighborhood exploration, permission-aware traversal
across many tenants, or per-tenant edge counts in the tens/hundreds of millions. A parallel
queue/OLTP/vector split trigger: sustained queue-depth backlog, or a p99 GAG-latency regression
caused by extraction-cascade write contention on the shared instance.

## Consequences

- **Easier:** one store to operate, back up, and reason about transactionally; "complete a job and
  enqueue its follow-ons" is a single local transaction; vector search co-locates with the records it
  ranks, so ACL filtering and tier ranking are plain SQL joins.
- **Easier:** zero extra infra to start, in line with the single-VPS hosting posture.
- **Harder / accepted:** deep multi-hop traversal and graph algorithms are awkward in SQL; we accept
  this because Phase 1–2 traversals are shallow, and we have written down the exact signals that
  justify migrating rather than guessing.
- **Accepted:** `edges` carries no foreign keys (it spans every node type), so referential integrity
  is an application invariant maintained by an orphan-GC job — a real cost we take on deliberately
  (see ADR 0003 and the edge-integrity job in the Pipeline Layer).
