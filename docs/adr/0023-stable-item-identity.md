# ADR-0023: A deterministic hierarchy for item identity

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

M1's primary source is a web page whose updates are table rows. Rows carry no
identifier of any kind.

If Microsoft reorders the table, rewords a heading, or restructures the markup, a
naive matcher sees new rows. M2 then mints **new permanent Feature IDs** for
knowledge already stored. Feature IDs are never reused and never renumbered
(ADR-0005), so duplicates created this way can never be cleaned up — the pack
carries them forever.

This is the most expensive mistake available in M1, and it is caused by a
*presentation* change rather than a knowledge change.

## Decision

Identity is computed from the most durable signal available, in a fixed order:

1. **Canonical target URL** — survives rewording, reordering and complete markup
   changes. Tracking parameters stripped, parameters sorted, fragment removed.
2. **Stable source identifier** — an id published by the source itself.
3. **Normalised title hash** — lower-cased, punctuation stripped, marketing noise
   removed, words **sorted**, so "Announcing general availability of Direct Lake"
   and "Direct Lake is now generally available" agree.
4. **Content fingerprint** — normalised title plus summary. Last resort.

**Every item records which basis was used** (`identity_basis`) and **what was
hashed** (`raw_value`), because the first question when investigating a duplicate
is always "what were we matching on?".

An item with none of the four raises `ValueError`. Better to fail loudly than to
fabricate an identity that will later collide.

## Consequences

### Positive
- **A presentation-layer change cannot create a duplicate permanent ID** whenever
  a URL is present — which, on the primary source, is nearly always.
- **`is_durable` distinguishes strong from weak identities**, so items resting on
  a title hash can be given closer attention when a source's markup changes.
- **Debugging is a diff, not an investigation**, because `raw_value` records the
  exact string that was hashed.
- Deterministic and pure: no clock, no network, trivially testable.

### Negative
- **Title-hash identity does not survive a verb change.** "X enters preview" and
  "X reaches GA" differ, because the noise list stops at nouns and adjectives.
  Extending it to every lifecycle verb would mean an ever-growing list that
  steadily raises the risk of two genuinely different features colliding — a
  worse failure than a missed match. This is precisely why the title hash is
  third rather than first, and the limitation is pinned by a test so it is not
  mistaken for a bug.
- **Dropping lifecycle words means "X preview" and "X GA" collapse** to one
  identity. Correct under ADR-0009 (one ID per concept, GA as a revision), but it
  is a judgement, not a fact.
- **A moved article changes its URL and therefore its identity.** Mitigated in M2
  by near-duplicate detection, which flags rather than drops (ADR-0014).
- The noise list is Microsoft-flavoured, so it lives in code rather than
  `pack.yml`. If a second vendor needs different words, it moves to configuration.

## Alternatives considered

**Row position or index.** Rejected outright: breaks on the first reorder.

**Raw title hash with no normalisation.** Rejected: Microsoft rewords
announcements constantly, so it would mint duplicates almost immediately.

**Full content hash as the only basis.** Rejected: changes whenever anything
changes, which is exactly when identity must *not* change.

**Ask an AI model whether two items are the same.** Rejected by ADR-0004 — no
model runs in the pipeline — and it would make identity non-deterministic, which
is worse than imperfect.
