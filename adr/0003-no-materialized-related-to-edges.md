# 0003. Do not materialize `related_to` similarity edges

- Status: Accepted
- Date: 2026-06-17

## Context

Semantic similarity between records is enormously useful for surfacing related knowledge, and it is
tempting to persist it as a `related_to` edge type alongside the curated edges (`derived_from`,
`supersedes`, `supports`, `contradicts`). Once you can compute a neighbor, why not store it?

Because similarity edges are **noisy, high-volume, and model/version-dependent**:

- They are O(n²)-ish to materialize and dominate edge count, turning the graph into a hairball where
  traversal becomes meaningless.
- They pin the graph to one embedding model — re-embedding (a more frequent, more expensive event
  than content edits) would invalidate every stored `related_to` edge.
- A stored similarity edge bakes in a threshold chosen at write time, which we cannot tune later
  without a full rewrite.

The curated edges, by contrast, are sparse, meaningful, and carry their own provenance and
confidence — they *should* be stored.

## Decision

**Do not materialize `related_to`.** Semantic neighbors are **computed at query time** from the
`embeddings` table, with a **stated similarity floor**, pinned to one embedding `model_version`. The
graph stores only the curated edge types (`derived_from`, `references`, `supersedes`, `supports`,
`contradicts`).

In Phase 3 GAG traversal, `related_to` is walked **only at depth 1** and only as a computed expansion,
never persisted. We will materialize `related_to` *only if* a concrete, measured latency/UX need
demands it — at which point it becomes its own ADR.

## Consequences

- **Easier:** re-embedding (blue/green per `model_version`) never invalidates stored edges; the
  similarity threshold and embedding model are tunable at query time; the `edges` table stays sparse
  and traversable, so orphan-GC and ACL traversal stay tractable.
- **Easier:** the graph is not pinned to one embedding model — a real architectural freedom given that
  model upgrades are expected.
- **Harder / accepted:** every query that wants neighbors pays an ANN lookup instead of an edge read.
  Acceptable: pgvector HNSW is fast, queries pin one version, and the floor bounds fan-out.
- **Guardrail:** an agent "optimizing" by persisting similarity edges is reversing a deliberate
  decision. Don't, without a new ADR backed by measured need.
