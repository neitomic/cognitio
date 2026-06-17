# Cognitio Design Review v2 — Implementation Critique of the Revised DESIGN.md

This reviews `DESIGN.md` (the v2 rewrite) against the two prior critiques (`REVIEW-cc.md`,
`REVIEW-codex.md`) and the original `DESIGN-v1.md`. Scope: did the rewrite fix the blocking
gaps, did it introduce new problems, what's still underspecified, is the phase plan sound, and
is this buildable as written.

**Bottom line up front:** This is a serious, high-quality revision. Nearly every blocking item
from both reviews is genuinely addressed — not name-dropped, but designed. The document went from
"happy-path sketch" to "engineering spec." It is close to build-ready for Phase 1. The remaining
issues are mostly *within-Phase-1 specification details* rather than architectural holes, with two
exceptions worth blocking on: **Gold has no representation in the data model**, and **entity-level
and group-level ACL semantics are incoherent as written**. Fix those (a day of design, not another
full pass) and start building.

---

## 1. Were the critical gaps actually fixed?

### Access control — **mostly fixed, with real holes** (see §3.1, §3.2)
First-class from Phase 1, ingested as `acl`/`acl_snapshot` per source version, propagated through
derivation (most-restrictive-wins), enforced at query time *before ranking and before content
reaches a prompt*, audited. This is exactly the shape both reviews demanded and it is now load-bearing
in the schema (`acl`, `effective_acl`) and the query layer. Credit where due. **But** the two hardest
parts of ACL — identity mapping across sources and group-membership resolution — are not addressed,
and entity-level ACL is self-contradictory. These are not nitpicks; they are where ACL systems
actually leak. Detailed below.

### Conflict / dispute lifecycle — **fully fixed.** This is the strongest part of the rewrite.
- Contradiction detection is now its own pipeline step with its **own confidence score** separate
  from extraction confidence, explicit thresholds, and a stated FP/FN policy. This directly answers
  REVIEW-cc §1.1.
- `disputed` is a first-class workflow state; the contradicted Gold is **not** silently overwritten
  or demoted, and the **query layer knows about dispute state** and returns alternatives with a
  warning instead of asserting one side. That was the single most important missing state in v1, and
  it's now modeled.
- The `conflicts` table is a real resolution unit handling multi-way/transitive conflicts, resolved
  as a set. This answers the "pairwise edges leave the set inconsistent" concern.

### Typed data model — **fixed in structure, partially fixed in queryability** (see §3.3)
The polymorphic `Node` is gone, replaced by `source_items / source_versions / normalized_documents /
extractions / entity_mentions / entities / edges / conflicts / review_items / embeddings /
change_events`. This matches the table breakdown REVIEW-codex recommended. Edges now carry
`confidence`, `provenance`, and temporal validity (`valid_from`/`valid_to`). `tenant_id`, `version`,
`is_current`, `node_type` are present from day 1. The overloaded `status` enum is split into three
orthogonal axes (`lifecycle`/`freshness`/`workflow`) — a clean, correct fix. **But** the core
extraction fields the critique wanted to be queryable (owner, due date, decision status) live inside
`extractions.payload` as JSON, and there is no representation of *Gold itself*.

### Evidence spans — **fixed.** Required on every extracted record, character offsets into the
*immutable* normalized text version, with an explicit verifier that **rejects records whose evidence
text does not match the source span**, plus deterministic fingerprints for idempotency. The
extraction output is now treated as untrusted until schema + span validation pass. This is precisely
what was asked for.

**Verdict on the four critical gaps:** conflict lifecycle and evidence spans are fully resolved;
typed data model and access control are resolved in architecture but have specific under-specified
seams that should be closed before coding (§3).

---

## 2. New issues introduced in the rewrite

### 2.1 Phase 1 scope ballooned but the estimate didn't move
The rewrite pulled **incremental sync, query-time ACL enforcement, entity mention+basic resolution,
observability, per-tenant cost accounting, and an eval harness** all into Phase 1 — correctly, on
the merits. But the effort estimate is still **"5–8 weeks for one experienced engineer,"** which is
the number the *original* REVIEW-codex gave for a Phase 1 that did **not** include incremental
re-derivation, ACL propagation/enforcement, or entity resolution. The scope grew materially; the
estimate is now internally inconsistent and optimistic. Incremental sync with chunk-level staleness
and resumable cascades is alone a multi-week subsystem. Either re-estimate Phase 1 at ~8–12 weeks
solo, or explicitly mark some of these (e.g. entity resolution, full eval harness) as "thin in P1,
hardened in P2" in the effort line, not just in prose.

### 2.2 `effective_acl` on `entities` is a new, unforced error
Adding `effective_acl` to the canonical `entities` table (introduced in this rewrite) creates a
semantic contradiction that didn't exist before. A canonical entity ("v1 API", "Alice") is the merge
of mentions across many sources with *different* ACLs. Apply most-restrictive-wins (intersection) and
a widely-referenced entity becomes invisible to nearly everyone because one private doc mentioned it;
apply union and you leak. Entity *existence/name* is usually not sensitive, but entity *description/
aliases* are derived from specific sources and can leak. This needs a deliberate split (public-ish
identity vs. provenance-scoped, ACL-bearing attributes) — not a single `effective_acl` column copied
from `extractions`. As written it will either over-hide or leak.

### 2.3 Polymorphic `edges` lose referential integrity
`edges(from_id, from_type, to_id, to_type)` spans every node type, so there can be no DB-level foreign
keys — orphan edges (pointing at superseded/deleted/archived rows) become an app-enforced invariant.
This is an acceptable, common tradeoff, but the doc should say it explicitly and name the orphan-GC /
integrity-check job, because the ACL and dispute logic both traverse edges and a dangling `contradicts`
edge has correctness (not just hygiene) consequences.

No other genuinely *new* problems — the rewrite did not trade one hole for another. The above are the
only regressions of note, and 2.2 is the only one I'd call a real defect.

---

## 3. Still missing or underspecified

### 3.1 ACL: identity mapping and group-membership resolution (the actual hard part) — **missing**
The design captures the source object's `acl` snapshot at fetch time and enforces against "the
requesting principal's permissions." Two things are unaddressed and they are exactly where these
systems fail:

- **Cross-source identity mapping.** A Cognitio user is one principal; the same human is a Notion
  user id, a Slack member id, a Google identity. Enforcing a Notion-derived record's ACL against a
  Cognitio principal requires mapping that principal to its Notion identity. There is no identity/
  principal-resolution component. Without it, "filter by the requesting principal's permissions" is
  underdefined the moment you have a second connector.
- **Group membership drift.** The doc says permission *changes on the object* trigger re-fetch. But
  most ACLs are expressed via *groups* ("Engineering", a teamspace, a channel), and membership changes
  happen in the identity provider / source workspace, **not** on the object — so they won't trigger an
  object re-fetch. A captured `acl_snapshot` listing group G, enforced by expanding G→members at fetch
  time, will leak to a removed member (stale snapshot) or block a newly-added one. Enforcement must
  resolve group membership **live at query time**, or subscribe to membership changes. The doc commits
  to neither. This is the single biggest correctness gap remaining and it sits in the Phase-1 "ACL
  enforced at query time" deliverable.

Also worth stating explicitly: most-restrictive-wins (intersection of viewer sets) is the *safe*
choice and the right default, but it over-restricts — a fact that legitimately also appears in a
public doc is hidden from people who can only see the public source. Acknowledge the utility cost so
it's a decision, not a surprise.

### 3.2 Right-to-deletion vs. immutable Tier 0 vs. content-hash idempotency — **mechanism unspecified**
The doc acknowledges the tension and says right-to-deletion "forces hard removal across the derivation
chain." But hard-deleting from an append-only, content-hashed, embedded store is non-trivial:
`source_versions` is immutable by design, fingerprints/`content_hash` may be derived from the deleted
content, and embeddings live in a separate index. There's no stated mechanism (crypto-shredding per
record? cascade hard-delete with tombstone fingerprints to prevent re-ingest?). Listed as "still open"
is fine for a deployment-policy detail, but the *deletion-cascade mechanics* are an engineering
question that Phase 1's immutability decisions constrain now.

### 3.3 Gold has no home in the schema — **this should block coding**
The central concept of the product is the tier ladder, and Tier 3 (Gold) is where trust lives. Yet:
- The tier table names storage for T0 (`source_versions`), T1 (`normalized_documents`), T2
  (`extractions`), and T3 *entities* — but there is **no table, column, or flag representing a
  decision/action/fact that has been promoted to Gold.** `extractions` has `lifecycle/freshness/
  workflow/version/is_current` but **no `tier` or `is_gold` or `promotion_state`**.
- So "return Gold > Extracted" ranking in the Query Layer, "auto-promote Extracted → Gold," and
  "disputed Gold" all reference a state the schema can't express. Presumably Gold = "an `extraction`
  with a confirming `review_item`," but then every "current Gold" query is a join against
  `review_items`, and a record can't be Gold-by-authoritative-source without a review row.

This is the most important remaining data-model gap. Add an explicit `tier`/`trust_state` column (or a
dedicated promotion record) to `extractions` so Gold is a first-class, indexable state. Right now the
schema can't answer the product's headline query ("show me the authoritative decisions").

### 3.4 Queryability of payload fields — **indexing strategy unstated**
REVIEW-codex's motivating example was "query all open action items with owners." In v2, `owner_entities`,
`due_date`, `status` live in `extractions.payload` (typed JSON), not columns. Postgres can do this with
GIN/expression indexes, but the design should *say so* and name which payload fields get promoted to
indexed generated columns — otherwise the operative queries are JSON scans and the critique's concern
is only half-resolved. "Schema-validated typed JSON" satisfies the letter of the recommendation; the
spirit (cheap structured queries over owner/due/status) needs an index plan.

### 3.5 Evidence-span verification is brittle as a pure string match
"Reject records whose evidence text does not match the source span" — exact match will reject on
whitespace, Unicode normalization, or trivial model paraphrase of the `text` field even when
`start_char`/`end_char` are correct. Specify the tolerance policy (normalize whitespace/Unicode;
verify by offsets-first, text as a checksum; define what "match" means). Otherwise the verifier
becomes a high-false-reject gate that silently drops good extractions.

### 3.6 Minor open seams
- **Contradiction scope:** detection is specified against existing Gold; two *Extracted* facts that
  contradict pre-promotion aren't covered. Probably fine for Phase 2, but state it.
- **Chunk-boundary context loss** is listed as a known failure mode with no mitigation (overlap /
  parent-doc context window / cross-chunk extraction pass). At least name the intended approach.
- **Entity merge/split** is called a "first-class operation" but has no representation (merge edges?
  audit rows? what happens to `entity_mentions.resolved_entity_id` and downstream fingerprints on a
  split?). Underspecified relative to its billing.
- **ACL vocabulary** mixes "union of source restrictions" and "intersection of viewer sets." These are
  consistent (union of *denies* = intersection of *allows*), but pick one framing and use it
  throughout; the mixed wording invites an off-by-one implementation bug in the exact place you can't
  afford one.

---

## 4. Is the phase plan realistic and correctly sequenced?

**Sequencing: yes, and the two ordering bugs from v1 are fixed.**
- **Incremental sync is now in Phase 1.** This was the central complaint of both reviews — v1's Phase 1
  was "RAG over Notion" and deferred the actual differentiator. v2 correctly makes delta detection +
  chunk-level staleness + per-record re-derivation the Phase 1 hypothesis-test. Right call.
- **Auto-promotion now requires conflict detection, and both are Phase 2.** v1 shipped auto-promote at
  confidence ≥ 0.9 in Phase 1 while conflict detection was Phase 2 — a correctness bug (auto-promote a
  fact that contradicts Gold with nothing to catch it). v2 makes Phase 1 *no auto-promotion, manual
  Gold only*, and gates Phase 2 auto-promotion behind conflict detection + a strict multi-condition
  rule. Correctly sequenced.
- GAG and Tier 4 synthesis are Phase 3 (async, rate-limited, temporal-validity-aware); ecosystem/
  webhooks/multi-tenant-activation are Phase 4. Reasonable.

**Realism: the sequence is right but Phase 1 is under-budgeted** (see §2.1). The risk is not wrong
ordering, it's that "Phase 1" now contains four subsystems each of which is independently substantial
(connector with reconciliation; incremental cascade; ACL propagation+enforcement; extraction with
validation+verification+fingerprinting), plus entity resolution, eval, observability, and cost
accounting. The plan would be more honest if it either (a) re-estimated to ~8–12 weeks solo, or (b)
explicitly scoped the day-1 thinness of entity resolution / eval / ACL-group-resolution so reviewers
know what "Phase 1 done" actually means.

One sequencing nit: query-time ACL enforcement is a Phase 1 deliverable, but its hard dependency
(identity mapping + group resolution, §3.1) is unscheduled. With a single connector and single tenant
you can fake principal mapping, but the deliverable should say "Phase 1 = single-connector ACL with
captured principal lists; cross-source identity mapping and live group resolution land with the second
connector in Phase 2" — otherwise it reads as more complete than it is.

---

## 5. Overall: ready to build from?

**Yes for Phase 1, conditional on closing a short list first.** This is no longer a vision doc; it's an
implementable spec with the right primitives (typed tables, evidence-bearing extraction, three status
axes, conflict sets, version-aware embeddings, cursor/capability connectors, cost controls). The
revision demonstrably absorbed both critiques rather than papering over them. Another *full* design
pass is not warranted — the architecture is sound.

**Close these before writing the schema (≈1 day of design):**
1. **Represent Gold.** Add an explicit `tier`/`trust_state` to `extractions` (§3.3). Without it the
   headline queries and the promotion/dispute logic have no column to stand on.
2. **Fix entity ACL** (§2.2): split public-ish identity from provenance-scoped, ACL-bearing attributes.
3. **Decide the ACL enforcement model** (§3.1): live group resolution and cross-source identity
   mapping, even if the implementation is staged — the *decision* can't be deferred because it
   constrains how `acl`/`effective_acl` are stored and indexed now.

**Close these early in Phase 1 (can be done in-flight):**
4. Payload indexing strategy (§3.4); evidence-match tolerance (§3.5); deletion-cascade mechanism
   (§3.2); re-estimate/re-scope Phase 1 effort (§2.1, §4).

Everything in §3.6 is polish that can ride along.

The design is now weakest exactly one layer down from where v1 was weak: v1 missed whole subsystems
(ACL, conflict lifecycle); v2 has the subsystems but leaves a few load-bearing *details* (Gold state,
group/identity resolution, deletion mechanics) unspecified. That's the normal and expected state of a
design that's ready to start building — provided the team treats the three blockers above as
schema-shaping decisions to make now, not later.
