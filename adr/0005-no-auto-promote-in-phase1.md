# 0005. No auto-promotion to Gold in Phase 1

- Status: Accepted
- Date: 2026-06-17

## Context

"Gold" is the product's headline concept: curated, authoritative knowledge. Its value depends
entirely on it being trustworthy. The tempting shortcut is to auto-promote high-confidence extractions
straight to Gold and let humans review only the rest.

This does not work, because **model confidence is not calibrated**. A model is routinely
*confidently wrong* about exactly the high-stakes attributes — owners, dates, and the
decision-vs-proposal distinction ("we should" ≠ "we decided"; "Alice can take this" may be a
suggestion). Promoting on confidence alone would quietly fill Gold with authoritative-looking errors,
destroying the one thing that makes Gold worth having.

Safe auto-promotion also has a hard prerequisite that does not exist yet in Phase 1: **conflict
detection**. You cannot auto-promote a fact without first checking it does not contradict existing
Gold, and contradiction detection is its own classifier step with its own error rate, scheduled for
Phase 2.

## Decision

**Phase 1 has no auto-promotion at all.** Gold is reached **only** by human confirmation:
one-click confirm → Gold (`trust_state = gold`, `gold_source = human_review`); edit + confirm →
corrected Gold with the override captured as an eval signal; reject → discarded as a negative example.
Every action writes to the `review_items` audit trail.

The `trust_state` column exists from day 1 (so a record can also be Gold via an authoritative source
without a synthetic review row), but the **auto_promoted** path is not wired.

Phase 2+ introduces **narrow** auto-promotion of low-risk simple facts only, gated on a conjunction of
**all** of: confidence ≥ 0.9, an exact verified evidence span, a source type allowed to be
authoritative, no unresolved pronouns or relative dates, no conflict with existing Gold (requires
Phase 2 conflict detection), and passing deterministic validation. Only `facts` of low-risk
`claim_type` (e.g. `state`, simple `metric`) ever qualify. **Decisions, policies, ownership,
deadlines, and customer commitments are never auto-promoted on confidence alone.**

## Consequences

- **Easier:** Gold means something from the first record; the MVP optimizes for trustworthy,
  evidence-first review; the override-rate metric (`review_items.before/after`) becomes a real quality
  signal from day 1.
- **Harder / accepted:** every plausible record requires human attention in Phase 1 — higher review
  load in exchange for trust. This is the correct trade for an MVP whose job is to *earn* trust.
- **Sequencing:** the promotion gate is built *after* the review lifecycle, not before — don't build
  the automation first. The gate code is stubbed and marked `# TODO(Phase 2)` so the conjunction is
  documented but inert.
- **Guardrail:** confidence ≥ 0.9 alone is never sufficient; the gate is a conjunction, and high-risk
  types are excluded regardless of confidence.
