# Architecture Decision Records

An **Architecture Decision Record (ADR)** captures a single significant architectural decision: the
context that forced it, the decision itself, and the consequences we accept. ADRs are immutable once
accepted — we don't rewrite history. If a decision changes, add a new ADR that supersedes the old one
and update the old one's status to `Superseded by NNNN`.

These records exist so that a developer or AI agent picking up a task understands **why** the system
is shaped the way it is, and does not silently reverse a deliberate decision (e.g. "let me just
materialize `related_to` edges" — see ADR 0003).

## Format

Each ADR follows the same structure:

```
# NNNN. Title

- Status: Accepted | Superseded by NNNN | Deprecated
- Date: YYYY-MM-DD

## Context
The forces at play: technical, product, cost. What makes this a real decision.

## Decision
What we are doing, stated plainly.

## Consequences
What becomes easier, what becomes harder, what we explicitly accept.
```

## How to add one

1. Copy the format above into `adr/NNNN-short-kebab-title.md`, using the next free four-digit number.
2. Write the context honestly — the trade-off, not just the conclusion.
3. Open it as part of the PR that implements (or commits to) the decision.
4. Once merged, treat it as immutable. Supersede; never edit the decision.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](./0001-postgres-over-neo4j.md) | PostgreSQL (+ pgvector) over a dedicated graph DB | Accepted |
| [0002](./0002-extraction-schema-v1.md) | `extraction.v1` structured-output schema with required evidence spans | Accepted |
| [0003](./0003-no-materialized-related-to-edges.md) | Do not materialize `related_to` similarity edges | Accepted |
| [0004](./0004-acl-inherited-union.md) | ACL on derived records is the union of source denies | Accepted |
| [0005](./0005-no-auto-promote-in-phase1.md) | No auto-promotion to Gold in Phase 1 | Accepted |
