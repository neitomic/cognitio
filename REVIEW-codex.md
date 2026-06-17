# Cognitio Design Review - Practical Implementation Critique

This is a promising product shape, but the design currently overstates how much value comes from "tiered graph" architecture and understates the hard parts: extraction correctness, provenance, source sync semantics, and review economics. The riskiest assumptions are not Postgres vs Neo4j. They are that model confidence is meaningful enough for auto-promotion, that every connector can provide clean deltas, and that extracted facts can be safely deduplicated, versioned, and contradicted without a much stricter schema.

## 1. Tech Stack Choices

### Postgres + pgvector is the right Phase 1 default

Start with Postgres. The proposed Phase 1 does not justify Neo4j operationally.

For Phase 1, Cognitio needs:

- Durable source ingestion state.
- Versioned raw/normalized content.
- Extraction records with provenance.
- Review queues.
- Basic edge traversal from extracted nodes back to source evidence.
- Semantic search over content/extractions.
- Idempotent jobs and retries.

Postgres handles all of that well. A normal adjacency-list table for edges, `jsonb` for connector metadata, `pgvector` for embeddings, `SKIP LOCKED` for worker queues, and recursive CTEs for shallow graph expansion are enough. Keeping the transactional store, queue, review state, and vector index in one database is a major simplification while the product is still proving whether users trust the output.

The doc is directionally right to avoid Pinecone/Qdrant/Neo4j early. Adding separate graph/vector infrastructure before the extraction and review loop is proven would mostly create distributed consistency problems.

### But "migrate to Neo4j if traversal performance matters" is too vague

The migration trigger should not be "traversal performance" in the abstract. Postgres will break down when the product needs graph-native operations that become central to the user experience:

- Deep multi-hop traversal, especially beyond 2-3 hops, with low latency.
- Path queries such as "show the chain of decisions that led to this policy."
- Graph algorithms: community detection, centrality, connected components, influence/risk propagation.
- High fan-out `related_to` edges from vector similarity, which can make traversal noisy and expensive.
- Interactive graph exploration where users pan/expand many neighborhoods live.
- Rich relationship predicates, temporal graph queries, or permission-aware traversal across many tenants.
- Large per-tenant graphs where edge counts are tens or hundreds of millions.

If Cognitio mostly retrieves source-backed facts, uses shallow provenance links, and assembles context for answers, Postgres is fine for a long time. If the graph itself becomes the product, Neo4j or another graph store becomes more attractive.

### The proposed schema needs more relational structure

The current `Node` table is too generic. A single `Node` abstraction is useful conceptually, but implementation should not shove every tier into one polymorphic table without typed child tables or strong constraints.

Recommended Phase 1 shape:

- `source_items`: one row per external object, stable connector/source id, current source state.
- `source_versions`: immutable raw snapshots, content hash, fetched metadata, source timestamps.
- `normalized_documents`: normalized text and chunk boundaries derived from a source version.
- `extractions`: structured extracted records, one row per decision/action/fact/entity/open question.
- `entities`: canonical entity records only after resolution, not every mention.
- `entity_mentions`: mention spans tied to source text.
- `edges`: typed relationships between extractions/entities/source versions, with evidence.
- `review_items`: workflow state, reviewer decisions, audit trail.
- `embeddings`: separate table keyed by object type/id/model/version.

The doc's generic `metadata: jsonb` is fine for connector-specific fields, but core fields like extraction type, evidence span, action owner, due date, decision status, and review state should be first-class columns or typed JSON validated by schema version.

### `related_to` edges should probably not be stored eagerly at first

Persisting semantic similarity as graph edges is tempting but dangerous. Similarity edges are noisy, high-volume, and model/version-dependent. In Phase 1, compute semantic neighbors at query time from embeddings. Store explicit `references`, `derived_from`, `supersedes`, and reviewed `supports`/`contradicts` edges. Only materialize `related_to` later if there is a concrete latency or UX need.

## 2. Extraction Pipeline

The extraction prompt in the doc is too vague to ship. It asks for "key decisions," "entities," "claims," and "sentiment" without defining what qualifies, how evidence is represented, how duplicate records are avoided, or what the model should do when the source is ambiguous.

The output schema needs to be boring, strict, and provenance-heavy. Every extracted object should be traceable to exact source spans. Anything without evidence should be rejected or marked as inference.

### A workable structured output schema

Use one response envelope per normalized document or chunk:

```json
{
  "schema_version": "extraction.v1",
  "source": {
    "connector": "notion",
    "source_id": "external-page-id",
    "source_version_id": "uuid",
    "chunk_id": "uuid",
    "title": "string"
  },
  "entities": [
    {
      "local_id": "ent_1",
      "name": "v1 API",
      "type": "person|team|product|system|customer|vendor|project|repository|document|metric|other",
      "aliases": ["string"],
      "description": "short source-backed description or null",
      "evidence_spans": [
        {
          "start_char": 128,
          "end_char": 134,
          "text": "v1 API"
        }
      ],
      "confidence": 0.0
    }
  ],
  "decisions": [
    {
      "local_id": "dec_1",
      "title": "Deprecate v1 API by Q3",
      "decision": "The team decided to deprecate the v1 API by Q3.",
      "status": "proposed|decided|reversed|superseded|unknown",
      "decision_date": "2026-06-17|null",
      "decision_makers": ["ent_2"],
      "affected_entities": ["ent_1"],
      "rationale": "short source-backed rationale or null",
      "constraints": ["string"],
      "evidence_spans": [
        {
          "start_char": 420,
          "end_char": 476,
          "text": "we're deprecating v1 API by Q3"
        }
      ],
      "confidence": 0.0
    }
  ],
  "actions": [
    {
      "local_id": "act_1",
      "description": "Alice will write the migration guide.",
      "owner_entities": ["ent_3"],
      "status": "open|in_progress|done|blocked|cancelled|unknown",
      "due_date": "2026-09-30|null",
      "related_entities": ["ent_1"],
      "source_language": "imperative|commitment|suggestion|inferred",
      "evidence_spans": [
        {
          "start_char": 500,
          "end_char": 536,
          "text": "@alice to write migration guide"
        }
      ],
      "confidence": 0.0
    }
  ],
  "facts": [
    {
      "local_id": "fact_1",
      "claim": "The v1 API is still used by enterprise customers.",
      "claim_type": "state|metric|policy|ownership|dependency|timeline|risk|other",
      "subject_entities": ["ent_1"],
      "qualifiers": {
        "time_scope": "current|null",
        "certainty": "certain|likely|uncertain",
        "scope": "all customers|enterprise customers|null"
      },
      "evidence_spans": [
        {
          "start_char": 220,
          "end_char": 268,
          "text": "enterprise customers still rely on v1"
        }
      ],
      "confidence": 0.0
    }
  ],
  "open_questions": [
    {
      "local_id": "q_1",
      "question": "Who owns the migration guide?",
      "related_entities": ["ent_1"],
      "status": "open|answered|unknown",
      "evidence_spans": [
        {
          "start_char": 600,
          "end_char": 636,
          "text": "who owns the migration guide?"
        }
      ],
      "confidence": 0.0
    }
  ],
  "relationships": [
    {
      "from_local_id": "dec_1",
      "to_local_id": "ent_1",
      "type": "mentions|affects|assigns|depends_on|supersedes|supports|contradicts",
      "evidence_spans": [
        {
          "start_char": 420,
          "end_char": 476,
          "text": "we're deprecating v1 API by Q3"
        }
      ],
      "confidence": 0.0
    }
  ],
  "warnings": [
    {
      "code": "ambiguous_owner|relative_date|missing_context|truncated_input|low_signal",
      "message": "string"
    }
  ]
}
```

Important implementation details:

- Require `evidence_spans` for every extraction.
- Store character offsets against the exact normalized text version, not the mutable source document.
- Use local IDs only within one model response, then map to durable database IDs after validation.
- Add deterministic fingerprints such as `hash(type + normalized_claim + evidence_span + source_version_id)` for idempotency.
- Validate the model response with JSON Schema or Pydantic before writing anything.
- Reject records where evidence text does not match the source span.
- Separate entity mentions from canonical entities. Entity resolution is a separate pass.

### Confidence is not enough for auto-promotion

The doc treats model confidence as if it were calibrated. It will not be. A model can be confidently wrong about owners, dates, decision status, and whether something was a decision versus a proposal.

Auto-promotion should be limited to low-risk facts with strong source evidence and no conflicts. For decisions, policies, ownership, deadlines, and customer commitments, "confidence >= 0.9" should not be enough. Use rules like:

- Must have exact evidence span.
- Must be extracted from a source type that is allowed to be authoritative.
- Must not be from comments unless comments are explicitly trusted.
- Must not depend on unresolved pronouns or relative dates.
- Must not contradict existing Gold.
- Must pass deterministic validation.

Even then, call it "source-backed extracted knowledge," not Gold, unless a human or an authoritative source marks it as such.

### Likely extraction failure modes

The design should explicitly handle these:

- Decisions vs proposals: "we should deprecate v1" is not the same as "we decided to deprecate v1."
- Relative dates: "next Friday," "Q3," and "end of sprint" need source timestamp and timezone.
- Implicit owners: "Alice can take this" may be a suggestion, not assignment.
- Pronouns and missing context: Slack/Notion comments often require thread/page context.
- Duplicate facts: the same decision appears in a meeting note, Slack recap, and project doc.
- Entity ambiguity: "Platform," "Core," "API," and first names collide across companies.
- Stale source truth: a deleted or edited doc may invalidate extracted nodes.
- Permission leaks: the extraction layer can create summaries that expose restricted content to users who cannot access the source.
- Table/list semantics: Notion databases and Google Sheets are not just text. Row-level fields matter.
- Low-signal extraction spam: the model will extract obvious or useless "facts" unless told not to.
- Over-normalization: paraphrases can erase legally or operationally important wording.
- Chunk boundary loss: actions/decisions can depend on context outside one chunk.

## 3. Connector Design

### `list_changes(since)` is necessary but not sufficient

The proposed connector interface is too clean for real SaaS APIs. A timestamp-based `list_changes(since)` has several problems:

- Some APIs do not support delta queries.
- Some APIs only expose `updated_at`, which may not change for comments, permissions, reactions, or child objects.
- Timestamps can be non-monotonic, delayed, rounded, or timezone-weird.
- API pagination can reorder results while syncing.
- Webhook delivery is at-least-once and can arrive before the changed content is fetchable.
- Deletions are often hard to discover.
- Permission changes are changes, even if content is unchanged.
- Rate limits force partial syncs and resumable cursors.

The connector contract should be cursor-based and capability-aware, not just timestamp-based.

### Better connector contract

Use something like:

```python
class Connector:
    def capabilities(self) -> ConnectorCapabilities:
        ...

    async def full_scan(self, cursor: str | None) -> Page[SourceRef]:
        ...

    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]:
        ...

    async def fetch(self, ref: SourceRef) -> SourceSnapshot:
        ...

    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]:
        ...

    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]:
        ...
```

Where each page returns:

- `items`
- `next_cursor`
- `high_watermark`
- `sync_started_at`
- `has_more`
- `retry_after`

And capabilities include:

- Supports incremental cursor.
- Supports updated-since filter.
- Supports webhooks.
- Supports tombstones/deleted objects.
- Supports permission metadata.
- Supports child object expansion.
- Supports stable content hashes.

For sources without delta queries, use periodic full scans with content hashing. This is acceptable for small Notion/Confluence workspaces and unacceptable for very large ones unless scoped by workspace, collection, database, channel, or folder. You need per-connector sync strategies:

- **Notion**: page/database traversal, `last_edited_time`, block children fetch, comments if available, periodic reconciliation for missed changes.
- **Slack**: conversations history plus replies; edited/deleted messages need event API or reconciliation windows.
- **Google Drive**: changes API is cursor-based; docs content fetch is separate.
- **GitHub**: webhooks plus REST/GraphQL updated cursors; comments/reviews have their own change surfaces.
- **Confluence**: page version APIs help, but comments/attachments/permissions need explicit treatment.

### You need idempotency and reconciliation from day one

The doc puts incremental indexing in Phase 2, but any connector shipped in Phase 1 still needs:

- A stored sync cursor or scan checkpoint.
- Idempotent change events.
- Content hashes to skip no-op fetches.
- Retry/backoff and dead-letter state.
- Periodic reconciliation scans to catch missed webhook/delta events.
- Tombstone handling for deletes and permission loss.

Without that, even the first connector will drift.

## 4. Cost Model

Using current public pricing as of 2026-06-17:

- Claude Sonnet 4.6: about `$3 / 1M input tokens` and `$15 / 1M output tokens`.
- `text-embedding-3-small`: about `$0.02 / 1M tokens`.

The design asks for a cost estimate for a 1000-document Notion workspace with a 10% daily change rate. The real answer depends on average document size and whether extraction is chunked, but a practical baseline looks like this:

### Baseline assumptions

- 1000 Notion pages.
- 100 changed pages per day.
- Average normalized page: 1,500 input tokens.
- Extraction prompt/instructions/schema overhead: 700 input tokens per request.
- Average extraction output: 500 tokens.
- One extraction call per changed page.
- One embedding pass per changed page.

Per document extraction cost:

- Input: `2,200 tokens * $3 / 1M = $0.0066`
- Output: `500 tokens * $15 / 1M = $0.0075`
- Total Claude extraction: about `$0.0141 / document`
- Embedding: `1,500 tokens * $0.02 / 1M = $0.00003`

Initial indexing:

- Claude: about `$14`
- Embeddings: about `$0.03`

Daily steady state at 10% changes:

- Claude: about `$1.41/day`
- Embeddings: about `$0.003/day`
- Monthly Claude: about `$42/month`

This is viable for a paid B2B product. It is not viable for a cheap self-serve plan if you also include retries, review UI operations, graph synthesis, answer generation, and support overhead.

### More realistic range

Notion workspaces are uneven. Some pages are tiny; some are long specs with tables and nested blocks. A safer range:

- Small pages, tight prompt, one pass: `$25-$50/month`.
- Medium docs, 2-3k tokens, 600-900 token output: `$60-$100/month`.
- Long docs with chunking, retries, entity resolution, contradiction checks, and synthesis: `$150-$300+/month`.

If the system runs separate passes for entities, decisions, actions, conflict detection, entity resolution, and synthesis, the cost can easily multiply by 2-5x. If every changed page triggers downstream synthesized-node regeneration, costs become harder to bound.

### Cost controls needed

The design should include these explicitly:

- Skip extraction for low-signal pages using cheap heuristics or a small classifier.
- Hash normalized chunks and only reprocess changed chunks.
- Use batch processing where latency allows.
- Cache static prompt/schema tokens if the provider supports it.
- Use cheaper models for entity mention extraction and reserve Sonnet for decisions/policies.
- Cap output length and number of extracted records per chunk.
- Avoid re-synthesizing Tier 4 nodes synchronously.
- Track cost per tenant/source/job in the database from day one.

The biggest hidden cost is not embeddings. It is repeated extraction plus human review time.

## 5. Phase 1 Buildability

The Phase 1 plan is buildable, but not as written. "Core graph + one connector" hides a lot of product-critical plumbing.

### Concrete components required

Backend/data:

- Postgres schema and migrations.
- Source item/version tables.
- Normalized document/chunk tables.
- Extraction tables with typed payloads and evidence spans.
- Review queue tables and audit log.
- Embedding table and vector index.
- Edge table for `derived_from`, explicit references, and reviewed relationships.
- Job queue using `SKIP LOCKED`, retry count, backoff, dead-letter state.
- Tenant/user model, even if single-tenant is hardcoded at first.

Connector:

- Notion OAuth or token configuration.
- Workspace/database/page discovery.
- Block tree fetcher.
- Block-to-normalized-text renderer preserving stable offsets.
- Checkpointed sync state.
- Content hashing.
- Periodic full reconciliation.
- Rate-limit handling.
- Delete/permission-change approximation.

Pipeline:

- Raw fetch job.
- Normalization job.
- Chunking job.
- Embedding job.
- Extraction job with strict JSON schema validation.
- Entity mention storage.
- Basic entity resolution/deduping.
- Staleness/invalidation for changed source versions.
- Observability: job logs, token usage, model latency, parse failures.

AI integration:

- Extraction prompt with examples.
- JSON Schema/Pydantic validation.
- Response repair/retry path for malformed JSON.
- Evidence span verifier.
- Deterministic fingerprinting for idempotency.
- Cost accounting per request.

API:

- Source sync status endpoints.
- Review queue endpoints.
- Confirm/edit/reject endpoints.
- Search endpoint.
- Source/evidence drilldown endpoint.

UI:

- Minimal review queue.
- Evidence viewer showing extracted claim next to source text.
- Edit form for decisions/actions/facts/entities.
- Reject/confirm controls.
- Basic semantic search page with source links.
- Sync/job status page for debugging.

Ops:

- Local dev setup.
- Migrations.
- Worker process.
- Environment/config management.
- Basic auth or single-user auth.
- Backups.
- Error monitoring/logging.

### Rough effort

For one strong full-stack engineer:

- Database schema, migrations, queue: 4-6 days.
- Notion connector with block rendering and sync state: 6-10 days.
- Normalization/chunking/embedding pipeline: 3-5 days.
- Extraction prompt, schema validation, retries, span verification: 5-8 days.
- Review API and audit trail: 4-6 days.
- Minimal React review/search UI: 6-10 days.
- Semantic search endpoint and result display: 2-4 days.
- Observability, cost accounting, admin/debug views: 3-5 days.
- Integration hardening and bug fixing: 5-10 days.

Realistic Phase 1: **5-8 weeks for one experienced engineer**, or **3-5 weeks for two engineers** if they are disciplined and avoid graph visualization/synthesis distractions.

If the goal is a demo, it can be faked in 1-2 weeks. If the goal is a usable internal alpha that does not silently corrupt trust, 5-8 weeks is a better estimate.

## What The Design Should Change

1. Keep Postgres + pgvector for Phase 1, but define explicit migration triggers for a graph database.
2. Replace the generic `Node` model with source/version/extraction/review tables and typed payloads.
3. Treat model output as untrusted until schema validation and evidence-span verification pass.
4. Do not auto-promote decisions/actions/policies based only on model confidence.
5. Move incremental sync basics into Phase 1. A connector without reconciliation will drift immediately.
6. Use cursor/capability-based connectors, not only `list_changes(since)`.
7. Separate entity mentions from canonical entities.
8. Avoid materialized `related_to` edges until there is a measured need.
9. Add per-tenant cost accounting in the first implementation.
10. Cut Tier 4 synthesis from early scope. It is expensive, hard to validate, and not needed to prove the core review/search loop.

## Bottom Line

The product should start as a source-backed extraction and review system with semantic search, not as a grand graph platform. Postgres is the right first store. The hard implementation work is building trustworthy extraction records with exact provenance, keeping connectors synchronized despite messy source APIs, and making review efficient enough that Gold knowledge means something.

The design is naive where it implies that confidence thresholds and a generic graph node model can carry trust. They cannot. The first version should optimize for boring durability, idempotent ingestion, strict schemas, and evidence-first review. The graph can become richer after users believe the extracted knowledge.
