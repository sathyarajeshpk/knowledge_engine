# ADR-0014: Flag near-duplicates, never drop them

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0 (decided) · M2 (implemented)

## Context

The same announcement reaches us through several routes. Microsoft publishes to
the Fabric blog, the Power BI blog, Microsoft Learn "What's New", and the docs
repository — often within days, with different titles and different URLs.
`CLAUDE.md` requires skipping duplicates.

Detection has three tiers of confidence:

1. **Exact URL match** after canonicalisation. Certain.
2. **Identical content fingerprint** — same normalised title and summary at a
   different URL. Near-certain.
3. **High textual similarity** — "Direct Lake now generally available" versus
   "Announcing GA of Direct Lake mode". Probable, but sometimes two genuinely
   different features with similar names.

The third tier is a judgement call, and any threshold produces false positives.
The question is what to do when the system is *probably* looking at a duplicate.

## Decision

**Layers 1 and 2 skip silently. Layer 3 never drops anything.**

A near-duplicate is stored as a normal knowledge object with
`needs_review: true`, listed in `indexes/review-queue.md`, and surfaced in the
weekly digest. The user decides. If it is a genuine duplicate, `ke supersede`
links it to the original and marks it `replaced` — and it stays in the
repository.

The similarity threshold lives in `pack.yml` as data:

```yaml
dedupe:
  near_duplicate_jaccard: 0.85
```

## Consequences

### Positive
- **"Never delete existing knowledge" is honoured in spirit, not just in
  letter.** Silently discarding an item that was never stored is not technically
  a deletion, but it is knowledge loss with no record — which is exactly what the
  rule exists to prevent.
- **False positives are recoverable.** A wrongly-flagged item is a review-queue
  entry, not a permanent absence. A wrongly-dropped item is invisible forever;
  you cannot review a decision you never saw.
- **The failure mode is bounded and visible.** Worst case is some review work.
  Compare with silent dropping, where the worst case is a missing tier-1 breaking
  change you never learn about.
- **The threshold is tunable without a release**, and its effect is observable in
  the review queue rather than in what is missing.
- **The user stays in control** of the one judgement the engine cannot make
  reliably without an AI model (ADR-0004).

### Negative
- **The review queue needs attention.** An ignored queue grows into noise.
  Mitigated by surfacing the count in the weekly digest and by `ke review` (M9).
- **Some genuine duplicates get stored**, consuming a Feature ID that is
  permanent even after the object is marked `replaced` (ADR-0005). Acceptable —
  IDs are cheap, lost knowledge is not.
- **`needs_review` is engine-owned**, so the user clears it via `ke supersede` or
  by editing, rather than by ignoring it.

### Neutral
- Layers 1 and 2 dropping silently is a deliberate asymmetry: those are
  *certain*, and a review queue full of certainties would train the user to
  ignore it.
- Jaccard similarity over title token sets is a crude measure. Crude is fine when
  the output is a suggestion rather than a decision.

## Alternatives considered

**Auto-drop above the threshold.** The obvious reading of "skip duplicates".
Rejected: the failure is silent and permanent. Losing a real feature announcement
because its title resembled last month's is exactly the outcome this project
exists to prevent, and there is no way to notice it happened.

**Auto-merge into the existing object**, appending as a revision. Appealing —
one object per concept, per ADR-0009. Rejected: merging two *different* features
into one object is much harder to undo than un-flagging a review item, and it
would corrupt the revision history with a change that never happened.

**Store with no flag and let the user find duplicates.** Rejected: throws away
information the engine actually has, and near-duplicates would accumulate
undetected.

**A higher threshold to reduce false positives.** Rejected as insufficient on its
own: any threshold has both kinds of error, and this ADR is about which kind is
survivable. With flagging rather than dropping, a *lower* threshold becomes safe
— more review items, no lost knowledge.

**Use an AI model to judge.** Would be more accurate. Rejected by ADR-0004: no
model runs in the scheduled pipeline.
