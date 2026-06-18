# Review: codebase-memory-mcp — what's worth borrowing for Cognitio

**Source:** https://github.com/DeusData/codebase-memory-mcp (DeusData, MIT, ~60K LOC of C/C++)
**Reviewed:** 2026-06-18
**Reviewer context:** read against Cognitio `DESIGN.md` and `NEXT_STEPS.md`.

## TL;DR

codebase-memory-mcp (CBM) and Cognitio are in different domains and most of the
architecture does **not** transfer. CBM extracts *deterministic* structure from
source code with parsers — cheap, exact, re-derivable, no human in the loop.
Cognitio extracts *probabilistic* knowledge from prose with an LLM — expensive,
untrusted, requiring provenance, review, and ACL. CBM therefore has essentially
nothing to teach about Cognitio's hardest problems (trust, Gold curation,
evidence spans, ACL propagation), where Cognitio is far more advanced.

But CBM is a mature, benchmarked, production knowledge-graph engine, and a handful
of its *mechanisms* are directly worth adopting:

1. **LSH (MinHash) candidate generation** — turns Cognitio's O(n²) entity-resolution
   blocking and contradiction-pair surfacing into O(n). Concrete, proven recipe.
2. **Hybrid lexical (BM25/FTS) + vector search** — Cognitio is pure-pgvector today;
   names/IDs/exact terms underperform. Postgres FTS is co-located and cheap to add.
3. **Validated edge-hairball discipline** — concrete per-node edge caps + similarity
   thresholds tuned against measured precision/recall. Cognitio is hand-wavy here.
4. **Deletion/reconciliation fail-safe taxonomy** — independent confirmation (incl. a
   real production incident) that Cognitio's "never infer deletion from a partial scan"
   rule is correct, plus a concrete preserve-on-uncertainty rule set to import.
5. **Eval methodology** — head-to-head graph-vs-baseline with an LLM-as-judge, which
   Cognitio's intrinsic-only eval plan lacks.

---

## 1. What codebase-memory-mcp is

**A local-first code-intelligence engine exposed as an MCP server.** Single static
C binary, zero runtime dependencies, no API keys. Full-indexes an average repo in
milliseconds, the Linux kernel (28M LOC) in ~3 min, answers structural queries in
<1ms. Pushes all *reasoning* to the calling agent (Claude Code etc.) — it has no LLM;
it just builds and serves the graph.

### Architecture
- **Parsing:** vendored tree-sitter grammars for 158 languages, compiled in. A second
  "Hybrid LSP" pass for 9 languages adds type-aware call resolution (import graph +
  per-file/cross-file definition registry) so `CALLS`/`USAGE` edges resolve like an
  IDE "Go to Definition."
- **Pipeline:** multi-pass — structure → definitions → imports → calls → usages →
  semantic (inherits/implements) → post-passes (tests, Louvain communities, HTTP/gRPC
  cross-service links, config, git history, infra-as-code).
- **RAM-first build:** indexing runs entirely in memory (LZ4-compressed reads,
  in-memory SQLite), dumped to disk once at the end, then memory is released.

### Data model (`src/store/store.c`)
A SQLite property graph, deliberately minimal:
- `nodes(id, project, label, name, qualified_name, file_path, start_line, end_line,
  properties JSON, UNIQUE(project, qualified_name))` — 13 labels (Function, Class,
  Route, Resource, …).
- `edges(id, project, source_id, target_id, type, properties JSON,
  UNIQUE(source_id,target_id,type))` — ~18 types (CALLS, IMPORTS, IMPLEMENTS,
  HTTP_CALLS, SIMILAR_TO, SEMANTICALLY_RELATED, …). Note: **real FKs with
  `ON DELETE CASCADE`** because every node lives in one table.
- `file_hashes(project, rel_path, sha256, mtime_ns, size)` — incremental change unit.
- `nodes_fts` — **FTS5 contentless BM25 index** with a camelCase/snake_case-aware
  tokenizer, for lexical search alongside vectors.
- A generated column (`url_path_gen` from `properties` JSON) for cheap structured
  filters — exactly Cognitio's "promote payload fields to generated columns" idea.

### Indexing approach (`src/pipeline/pipeline_incremental.c`, `src/watcher/`)
- **Change detection:** a git-polling watcher (HEAD movement + dirty working tree +
  submodules) with adaptive intervals (5s + 1s/500 files, capped 60s) and a
  non-blocking index lock.
- **Incremental unit = file.** Classify each file by `(mtime_ns, size)` against
  `file_hashes` (cheap stat, no read); for changed files, **delete the file's nodes
  (edges cascade), re-parse only those files**, merge into the DB, persist new hashes.
- **Semantic search without external models:** an 11-signal combined score (TF-IDF,
  Random Indexing co-occurrence, MinHash structural, API/type/decorator signature
  vectors, AST structural profile, approximate data-flow, graph diffusion,
  Halstead-lite, module proximity). Also bundles `nomic-embed-code` (768-d int8)
  compiled into the binary.
- **Near-clone detection:** MinHash (K=64) + LSH (32 bands × 2 rows) → `SIMILAR_TO`
  edges at Jaccard ≥ 0.95.
- **Query:** a read-only openCypher subset (planner/executor in `src/cypher/`),
  variable-length paths capped at depth 10, with explicit `unsupported …` errors
  rather than silent empty results.

---

## 2. Patterns / ideas / code worth adopting

### 2.1 LSH (MinHash) blocking for O(n) candidate generation — **highest value**
`src/simhash/minhash.h` is a clean, dependency-free MinHash + LSH implementation:
K=64 signatures, banding (b=32 × r=2, threshold ≈ (1/b)^(1/r)), `cbm_lsh_query`
returns near-neighbors without an all-pairs scan.

Cognitio has **three** places that currently say "compare candidates by similarity"
without specifying how to *generate* candidates without O(n²) blow-up:
- Entity resolution: *"blocking (name/alias normalization) + embedding similarity"*
  (DESIGN §Entity resolution).
- Contradiction detection: *"candidate fact pairs surfaced by semantic similarity +
  shared subject entities"* (DESIGN §Conflict & dispute).
- Fact dedup — the explicit worry that *"14 discussions may be 6 distinct decisions
  echoed."*

LSH over MinHash of normalized claim text (or entity name n-grams) is the standard,
cheap way to do this blocking step. **Takeaway:** add a MinHash/LSH (or pgvector ANN
+ shared-entity) blocking index as the explicit first stage of both entity resolution
and contradiction-candidate surfacing. Postgres can do this with `pg_trgm`/GIN for the
lexical block and the existing `embeddings` ANN for the semantic block; CBM's banding
math (`threshold ≈ (1/b)^(1/r)`, K=64 → ±0.12 Jaccard SE) is the tuning reference.

### 2.2 Hybrid lexical (BM25) + vector retrieval
CBM runs **FTS5/BM25 with a code-aware tokenizer in parallel with vector search**.
Cognitio's query layer (DESIGN §Query Layer) is **pure pgvector**. Pure-vector
retrieval is well known to miss exact-term queries: a person's name, a product code,
a ticket ID, an error string. Postgres ships `tsvector`/GIN in the same DB, so a
hybrid retriever (RRF or weighted fusion of ANN + FTS) is nearly free infra-wise and
materially improves recall for the exact kind of "find the decision about *Project
Atlas*" queries company-knowledge users will type. **Takeaway:** make semantic search
*hybrid* lexical+vector, both ACL-filtered before ranking.

### 2.3 Edge-hairball discipline with concrete, validated numbers
CBM enforces, for every inferred edge type, **(a) a similarity threshold and (b) a
hard max-edges-per-node cap** (`SEMANTICALLY_RELATED`: ≥0.75, ≤10/node;
`SIMILAR_TO`: ≥0.95, ≤10/node), and it has the precision/recall data to justify them
(0.80 → 100% precision but 90 edges; 0.70 → 80% precision, 2047 edges).

Cognitio correctly keeps `related_to` *computed, not stored*. But `supports` and
`contradicts` **are** materialized, and the design specifies a per-edge confidence and
a query-time fan-out cap in GAG — **but no cap on how many `supports`/`contradicts`
edges a single hot record may accumulate at write time**, and no stated threshold
methodology. A heavily-echoed Gold fact could sprout hundreds of `supports` edges.
**Takeaway:** specify a max-edges-per-node cap + a precision-tuned confidence floor
for materialized `supports`/`contradicts`, mirroring CBM's discipline.

### 2.4 Cheap stat-style pre-gate before expensive fetch
CBM never reads/hashes a file whose `(mtime, size)` is unchanged. Cognitio's
incremental path is built on `content_hash` + per-`chunk_hash` (good), but the
connector contract should make the **pre-fetch** gate explicit: for Notion, use
`last_edited_time` (+ child-count/size hints) to skip *fetching the block tree at all*
for unchanged pages, before any hashing. DESIGN mentions `last_edited_time`; the cheap
"don't even fetch" gate deserves to be a first-class step in the connector spec.

### 2.5 Evaluation: head-to-head + LLM-as-judge
CBM's `docs/EVALUATION_PLAN.md` runs the product (graph tools) against a **baseline
condition (grep/glob/read only)** on the *same questions*, graded by a **blind
LLM-as-judge**, and reports token/tool-call deltas (claims 10× fewer tokens, 2.1×
fewer tool calls). Cognitio's eval (DESIGN §Evaluation) is strong on *intrinsic*
metrics (extraction precision/recall, override rate) but has **no head-to-head test
proving GAG actually beats plain RAG**. **Takeaway:** add an A/B eval — GAG-with-graph
vs. flat semantic RAG on the same questions, LLM-as-judge — to prove the graph earns
its complexity. Also borrow their ops hygiene: one flat results tree overwritten each
run, history in git, no versioned `vN/` dirs.

### 2.6 Query-API failure semantics
CBM's Cypher subset returns explicit `unsupported …` errors rather than silently
returning empty results for unhandled syntax. When Cognitio's external query API
ships (Phase 4), adopt the same principle — silent-empty is indistinguishable from
"no matches" and erodes trust.

---

## 3. What CBM does differently / better

- **Determinism removes whole subsystems.** CBM's knowledge is re-derivable by
  re-parsing, so it needs *no* review queue, trust tiers, evidence spans, or ACL
  propagation. This is *why* it can auto-everything — and why it has nothing to offer
  Cognitio on those axes (Cognitio is correctly far heavier there).
- **Concrete thresholds and caps** where Cognitio is qualitative (see 2.3).
- **Hybrid lexical+vector retrieval** vs. Cognitio's vector-only (see 2.2).
- **FK `ON DELETE CASCADE` for edges** — because all nodes share one table, CBM gets
  referential integrity for free. Cognitio's polymorphic `edges` (spanning every node
  type, no FKs) deliberately can't, which is exactly why Cognitio needs the
  edge-integrity/orphan-GC job. CBM is a useful reminder that this GC job is doing
  real work that a single-table design gets for nothing — keep it well-tested.
- **Performance posture:** RAM-first build-then-dump, sub-ms queries, single binary.
  Not portable to Cognitio's multi-tenant Postgres/VPS model, but the RAM-first
  batch-build pattern is a reasonable shape for Cognitio's blue/green re-embed job.
- **Distribution as an MCP server with no built-in LLM**, delegating reasoning to the
  caller. Strategically interesting for Cognitio's Phase 4 external query API: shipping
  an MCP surface lets any agent do the synthesis over ACL-filtered Gold, rather than
  Cognitio owning the whole answer-generation stack.

What Cognitio does better / that CBM lacks entirely: provenance with exact evidence
spans, trust tiers + human review, ACL propagation (most-restrictive-wins), conflict/
dispute lifecycle, multi-tenancy, cost accounting, crypto-shredding deletion. None of
this exists in CBM because its domain doesn't need it.

---

## 4. Gaps in our DESIGN.md / NEXT_STEPS.md that this reveals

| # | Gap | Severity | Where | Fix |
|---|-----|----------|-------|-----|
| A | **No lexical/BM25 retrieval** — query layer is pure pgvector; exact names/IDs/terms will under-rank. | Medium | DESIGN §Query Layer; NEXT_STEPS Step 7 | Add Postgres FTS (`tsvector`/GIN) and fuse with ANN (RRF), both ACL-filtered pre-rank. |
| B | **Candidate-generation/blocking for entity resolution and contradiction pairs is unspecified** ("by similarity" with no algorithm or complexity). | Medium | DESIGN §Entity resolution, §Conflict detection | Specify an LSH/ANN + shared-entity blocking stage; schedule building the blocking index in the Phase 2 entity-resolution/contradiction work. |
| C | **No write-time fan-out cap or threshold methodology for materialized `supports`/`contradicts` edges** — only `related_to` is addressed. | Medium | DESIGN §Edges, §Query Layer | Add max-edges-per-node cap + precision-tuned confidence floor; tune against the contradiction-detector eval set. |
| D | **Eval has no head-to-head graph-vs-baseline comparison or LLM-as-judge** — only intrinsic precision/recall. The vision's "the graph is more useful" claim isn't directly measured. | Medium | DESIGN §Evaluation | Add an A/B (GAG vs flat RAG) judged blind by an LLM; track token/tool-call/answer-quality deltas. |
| E | **Cheap pre-fetch gate not explicit** — incremental relies on content/chunk hashes, but the connector contract should skip *fetching* unchanged subtrees via `last_edited_time`/size before hashing. | Low | DESIGN §Source Connectors; NEXT_STEPS Step 4 | Make the pre-fetch skip a named step in the connector capability/scan spec. |
| F | **Deletion fail-safe is stated at a high level but not as a rule set.** CBM had a real incident (a fast/partial scan purged "mode-skipped" items) and now distinguishes truly-deleted (`stat` ENOENT/ENOTDIR) from not-visited, and **preserves on any uncertainty** (transient errno, truncation). | Low (validation) | NEXT_STEPS Step 4 ("never infer deletion from a failed/partial scan") | Import the concrete preserve-on-uncertainty taxonomy into the reconciliation/tombstone handler; add a test for the partial-scan-doesn't-archive case. This *confirms* our design rather than contradicting it. |
| G | **External query API failure semantics undefined** — risk of silent-empty on unsupported queries. | Low | DESIGN §Query Layer (Phase 4) | Adopt explicit `unsupported …` errors. |

### Net assessment
Cognitio's core architecture is sound and substantially *ahead* of CBM on every
trust/provenance/ACL dimension — CBM validates rather than challenges the big
decisions (typed nodes, computed `related_to`, append-only versioning, the
reconciliation-is-source-of-truth + never-infer-deletion-from-partial-scan rules).
The genuinely actionable borrows are tactical: **(1) LSH/ANN blocking for ER and
contradiction candidates, (2) hybrid BM25+vector search, (3) concrete edge caps +
thresholds, (4) a head-to-head + LLM-judge eval.** None requires changing the schema
or the phase plan; all slot into existing Phase 1–2 work.
