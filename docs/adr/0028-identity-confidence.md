# ADR-0028: Identity Confidence gates minting

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1
**Relates to:** ADR-0027 (three-level model), ADR-0023 (identity hierarchy), ADR-0005 (Feature IDs)

## Context

ADR-0027 established that an announcement may report many features, and that a
collision must never become a silent merge. That says what to do when identity is
*ambiguous*. It does not say who decides whether a given identity is good enough
to mint a **permanent** Feature ID from.

`identity_basis` records what an identity rests on, but a basis alone cannot
separate the two cases that matter:

* a canonical URL that identifies exactly this feature — safe to mint
* a canonical URL that identifies a blog post covering eleven features — not safe

Both are `canonical-url`. Under ADR-0023 both look equally durable.

Since Feature IDs are permanent and never reused, minting on an ambiguous
identity is unrecoverable. The engine needs a decision point that is *not* part
of the identity itself.

## Decision

Introduce **`IdentityConfidence`**: `high` · `medium` · `low`.

### The distinction that makes it sound

> **Identity is permanent and run-independent.
> Confidence is a per-run assessment of whether minting is safe yet.**

This resolves a tension that would otherwise be fatal. ADR-0027 rejects
run-scoped evidence — *how many features share this announcement in this run* —
as an input to **identity**, because an identity that changes when a row stops
appearing is not permanent.

The same evidence is legitimate for **confidence**, precisely because confidence
never enters the ID. It decides only whether to mint *now*. Once minted, the
identity is fixed forever regardless of what confidence does later.

- Confidence **may** use run-scoped evidence. Identity **may not**.
- Confidence is recomputed every run. Identity is computed once, ever.
- A change in confidence never changes an existing Feature ID.

### The levels

```
HIGH    durable anchor (canonical-url | source-identifier)
        AND the announcement resolves to exactly one distinct feature this run

MEDIUM  durable anchor BUT the announcement is shared by several features
        OR a weak anchor (title hash) with a substantive title

LOW     content-fingerprint anchor, or no usable title and no durable anchor
```

**Named, not scored.** A numeric score invites a tunable threshold, and a tunable
threshold is a dial someone eventually turns to make a review backlog go away.
Three named states with recorded reasons cannot be quietly relaxed.

Every item also carries `confidence_reason` in words. A person triaging the
queue should not have to re-derive the rule that put an item there.

### The mint gate

```
mint automatically  ⟺  identity_confidence is HIGH
                       AND source_authority is OFFICIAL
otherwise           →  review queue, never dropped
LOW                 →  never minted automatically, under any setting
```

The gate is a **combination**, not one overloaded field. `identity_confidence`,
`date_confidence` and `source_authority` answer three different questions, and
folding them together would mean none could be changed without redefining the
others — the same reasoning that separated `date_precision` from
`date_confidence` in ADR-0017.

### Where it is computed

In `discover_all`, after the whole run is collected — not in adapters. An adapter
sees one source at a time and cannot know whether an announcement is exclusive.
This is enforced by construction: adapters leave the field at its default.

### Queued items keep their first discovery date

An item queued as Medium may become High in a later run and mint then. If the ID
month came from *that* run, review latency would silently shift the Feature ID —
an item found in July but approved in September filed under September, forever.

A queued item must therefore record its **first** discovery date, and minting
must use that. The ID reflects when the knowledge appeared, never how long a
human took to look at it.

## Consequences

### Positive

- **No permanent Feature ID is created while identity uncertainty exists** —
  which was the requirement.
- **The ratio is usable.** Measured against production: 79% of Fabric items and
  73% of Power BI items are High and mint automatically; the rest queue. A gate
  that queued most of the pack would be a gate nobody uses.
- **Per-item beats per-source.** Power BI needs no source-level exclusion: its 14
  well-anchored items mint and its 5 weak ones queue, rather than the whole
  source being trusted or distrusted wholesale.
- **Deterministic and offline.** Same items in, same grading out. No model
  (ADR-0004), no clock, no network.
- **Reasons travel with items**, so the review queue explains itself.

### Negative

- **A queue that nobody drains is a slower form of data loss.** Roughly 20% of
  discovered knowledge now waits on a human. M2 owes this a real path, and the
  digest should surface queue depth so neglect is visible.
- **`LOW` currently matches nothing.** No present source produces titleless rows.
  It is a floor for sources not yet onboarded, not a level doing work today, and
  is reported that way rather than presented as a success.
- **Confidence depends on what else was discovered in the same run.** Two runs
  over different source sets can grade the same item differently. This is sound
  — confidence is explicitly a per-run assessment — but it is surprising, and
  anyone reading a stored confidence value must read it as "as of that run".
- **One more field per item**, and a second concept named "confidence" alongside
  `date_confidence`. The names are similar and the things are unrelated; the
  glossary has to carry that weight.

## Alternatives considered

**A numeric confidence score with a threshold.** Rejected: see "named, not
scored". The failure mode is social, not technical — thresholds get lowered.

**Fold `date_confidence` and `source_authority` into one score.** Rejected: three
orthogonal questions collapsed into one number, none separately adjustable, and
the resulting value would explain nothing.

**Gate on source rather than item.** This is what disabling the Power BI fallback
did. Rejected as the general mechanism: it discards good items to avoid bad ones,
where a per-item gate keeps the 73% that are fine.

**Mint everything and repair later.** Impossible by construction — Feature IDs
are never reused or renumbered (ADR-0005), so there is no "later".
