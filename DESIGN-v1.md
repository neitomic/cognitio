# Cognitio — Design Document

## Vision

A living knowledge platform for companies. Unlike static RAG systems that index documents once and drift out of date, Cognitio maintains a continuously updated, tiered knowledge graph where content from documents, discussions, and comments flows in incrementally and is progressively distilled into higher-quality, structured knowledge nodes.

The model is a **collaborative pair**: AI proposes enrichments and connections at every tier; humans confirm or override high-stakes ones. Over time the graph becomes more accurate, more connected, and more useful — not less, as it would with a stale index.

---

## Core Concept: Tiered Knowledge Graph

Every piece of knowledge in Cognitio lives as a **node** in a graph. Nodes have a **tier** that reflects how processed and trusted the knowledge is.

### Tiers

| Tier | Name | Description |
|------|------|-------------|
| 0 | **Raw** | Ingested as-is from source systems. A Notion page, a Slack thread, a GitHub PR, a Confluence doc. Immutable — changes to source create a new version, not an in-place edit. |
| 1 | **Structured** | Cleaned and normalized. Markdown stripped to plain text, metadata extracted (author, timestamp, source, tags), language detected. Still 1:1 with source. |
| 2 | **Extracted** | Entities, facts, decisions, and relationships pulled out by the model. A Slack thread becomes: `[Decision: we're deprecating v1 API by Q3]`, `[Action: @alice to write migration guide]`, `[Entity: v1 API]`. Multiple extracted nodes per raw node. |
| 3 | **Gold** | Curated, human-confirmed facts. Promoted from Tier 2 by a human reviewer (or auto-promoted if confidence is high and no conflict exists). These are the authoritative knowledge atoms. |
| 4 | **Synthesized** | Cross-source summaries, trend analyses, or derived insights that span multiple gold nodes. E.g. "The team's stance on API versioning, synthesized from 14 discussions over 6 months." |

### Edges

Edges connect nodes across and within tiers:

- `derived_from` — Tier 2+ node was derived from this Tier 0/1 node
- `supports` / `contradicts` — Two gold nodes agree or conflict
- `references` — A document explicitly mentions another concept or entity
- `supersedes` — A newer node replaces an older one
- `related_to` — Semantic similarity (vector-based, weighted)

---

## Incremental Indexing

The key differentiator. When a source changes:

1. **Delta detection** — compare new content to last-indexed version (hash or diff). Only changed chunks are reprocessed.
2. **Invalidation propagation** — any Tier 2+ nodes derived from the changed raw node are marked `stale` and queued for re-derivation.
3. **Re-derivation** — the model re-extracts entities/facts from the updated content. New extracted nodes are compared to old ones: unchanged facts are kept, changed facts are versioned, new facts are added.
4. **Conflict detection** — if a re-derived fact contradicts an existing gold node, flag for human review rather than silently overwriting.
5. **Downstream cascade** — synthesized nodes that depended on changed gold nodes are re-queued for synthesis.

This means a single Slack message edit triggers a targeted, cheap update — not a full re-index of the knowledge base.

---

## Source Connectors

Pluggable connectors ingest from external systems. Each connector implements:

- `list_changes(since: timestamp) → [ChangeEvent]` — pull-based delta fetch
- `fetch_content(id) → RawNode` — fetch full content for a changed item
- `subscribe(webhook) → void` — optional push-based for real-time sources

**Initial connectors:**
- Notion (pages, databases)
- Confluence (pages, comments)
- Slack (channels, threads)
- GitHub (issues, PRs, discussions, wiki)
- Linear (issues, comments)
- Google Drive (Docs, Sheets)

Connectors are isolated — adding a new one doesn't touch the graph schema.

---

## AI Extraction Pipeline

For each Tier 1 → Tier 2 transition, the model runs extraction with a schema-guided prompt:

```
Given this content from [source], extract:
- Key decisions made
- Action items with owners
- Named entities (people, systems, products)
- Factual claims
- Open questions
- Sentiment/stance on topics

For each extraction, rate your confidence (0.0–1.0).
```

Extracted nodes below a confidence threshold are flagged as `pending_review`. Above threshold, they are auto-promoted with a `model_proposed` label.

**Human review flow:**
- Reviewer sees a queue of `model_proposed` nodes grouped by topic
- One-click confirm → becomes Gold
- Edit + confirm → corrected Gold, feedback fed back to improve future extractions
- Reject → discarded, negative example stored

---

## Query Layer

Once the graph is built, two query modes:

### 1. Semantic Search
Vector embeddings on Tier 1+ nodes, HNSW index for ANN search. Returns ranked nodes with tier, source, confidence, and recency.

### 2. Graph-Augmented Generation (GAG)
Unlike plain RAG, queries traverse the graph:

1. Find seed nodes via semantic search
2. Walk edges: pull in `supports`, `derived_from`, `related_to` neighbors up to depth N
3. Rank by tier (Gold > Extracted > Structured) and recency
4. Inject into model context with provenance metadata

This means a query about "our API versioning policy" doesn't just return the most similar paragraph — it returns the confirmed decisions, the discussions that led to them, and any open conflicts, all linked.

---

## Human-in-the-Loop: Opt 3

Three escalation levels:

| Level | Trigger | Action |
|-------|---------|--------|
| **Auto-promote** | Confidence ≥ 0.9, no conflict | Tier 2 → Gold immediately, logged |
| **Soft review** | 0.7 ≤ confidence < 0.9 | Gold with `needs_verification` label, shown in review queue |
| **Hard gate** | Confidence < 0.7, or conflicts existing Gold | Stays at Tier 2, blocks until human acts |

Reviewers get a daily digest of pending nodes. Power users can set per-topic confidence thresholds.

---

## Data Model (high-level)

```
Node {
  id: uuid
  tier: 0–4
  content: text
  content_hash: sha256
  source_connector: string
  source_id: string          # external ID in the source system
  source_url: string
  author: string
  created_at: timestamp
  indexed_at: timestamp
  status: active | stale | archived | pending_review
  confidence: float          # for Tier 2+
  embedding: vector[1536]
  metadata: jsonb
}

Edge {
  id: uuid
  from_node: uuid
  to_node: uuid
  type: derived_from | supports | contradicts | references | supersedes | related_to
  weight: float
  created_by: human | model
  created_at: timestamp
}

ChangeEvent {
  id: uuid
  connector: string
  source_id: string
  changed_at: timestamp
  change_type: created | updated | deleted
  processed_at: timestamp?
  status: pending | processing | done | failed
}
```

---

## Tech Stack (proposed)

| Layer | Choice | Reason |
|-------|--------|--------|
| Graph store | **Neo4j** or **PostgreSQL + pgvector** | Start with Postgres for simplicity; migrate to Neo4j if traversal performance matters |
| Vector search | **pgvector** (co-located with graph) | Avoid managing a separate Pinecone/Qdrant unless scale demands it |
| Extraction model | **Claude claude-sonnet-4-6** | Structured output, high accuracy for entity/decision extraction |
| Embeddings | **text-embedding-3-small** | Cost-efficient for large corpora |
| Ingestion pipeline | **Python + async workers** | Simple queue-based, easy to add connectors |
| Queue | **PostgreSQL (SKIP LOCKED)** | Same DB, zero infra overhead to start |
| API | **FastAPI** | Familiar stack |
| Review UI | **Simple React SPA** | Review queue, graph explorer |
| Hosting | **Single VPS to start** | Keep it cheap until there's signal |

---

## Phase Plan

### Phase 1 — Core graph + one connector
- Data model + Postgres schema
- Notion connector (list_changes + fetch_content)
- Tier 0→1 normalization
- Tier 1→2 extraction (Claude)
- Manual review UI (minimal)
- Semantic search over Tier 1+

### Phase 2 — Incremental indexing
- Delta detection + invalidation propagation
- Conflict detection on Gold updates
- Change queue with retry/backoff
- Second connector (Slack or GitHub)

### Phase 3 — Graph-augmented generation
- Edge traversal in query path
- Graph-aware context assembly
- Provenance in responses ("This decision was made in [Slack thread], confirmed by [Confluence doc]")

### Phase 4 — Ecosystem
- Connector SDK (3rd-party connectors)
- Webhook support for real-time sources
- API for external tools to query the graph
- Multi-tenant

---

## Open Questions

1. **Graph schema flexibility** — how do we let Gold node types evolve without breaking existing nodes? Probably: typed nodes with a `node_type` field + `properties: jsonb`, schema registered per type.
2. **Deletion handling** — when a source document is deleted, do we archive its derived nodes or keep them (they may still be valid facts)?
3. **Cross-tenant knowledge** — if this becomes multi-tenant, some gold nodes might be shareable (e.g. public docs). Opt-in only.
4. **Embedding freshness** — embeddings need to be recomputed when content changes. Track `embedding_version` per node.
5. **Cost model** — extraction runs Claude on every change. Need a cost estimate per company/month at realistic change rates.
