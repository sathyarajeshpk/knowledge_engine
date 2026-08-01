# ADR-0035: Supersession is a status, not an acquisition stage

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M5
**Amends:** [ADR-0029](0029-knowledge-lifecycle.md)

## Context

ADR-0029 introduced `Lifecycle` with six stages, one of which was `SUPERSEDED`,
and flagged in its own Negative consequences that the name collided with
`status: replaced`:

> lifecycle `superseded` means *this acquisition record* was replaced by a later
> one for the same feature; `status: replaced` means *this feature* was replaced
> by a different feature. The names are close enough to be confused, and the
> glossary has to carry that. This is the weakest part of the design and worth
> revisiting in M5 when supersession is actually implemented.

M5 implemented it. The question settled itself.

## Decision

**Remove `Lifecycle.SUPERSEDED`.** Supersession is recorded entirely in
`status: replaced` plus the `replaced_by` / `replaces` pair.

The reasoning is short: an object whose *feature* was replaced has still been
fully **acquired**. Nothing about its journey through discovery, queueing,
approval and minting changed. Its lifecycle is `MINTED` and stays `MINTED`.

`Lifecycle` now reads:

```
discovered → queued → approved → minted → archived
```

Confirmed before removal: nothing in the engine referenced the value, and no
stored object carried it. It was speculative from the day it was written.

## Consequences

### Positive
- The one genuine ambiguity in the lifecycle model is gone.
- Each axis answers exactly one question again: `lifecycle` = how far through
  acquisition; `status` = is this knowledge current.
- A superseded object is visibly `minted` + `replaced`, which is the honest
  description of it.

### Negative
- **`Lifecycle` no longer has a terminal state between `minted` and `archived`.**
  If a future stage genuinely needs one — a re-acquisition pipeline, say — it
  will be added then, with a use rather than in anticipation of one.
- Removing an enum value is technically an on-disk format change. Safe here only
  because it was never written; the same removal after M6 would need a migration.

### Neutral
- `ARCHIVED` remains, and is still written by nothing. Unlike `SUPERSEDED` it
  has an unambiguous meaning and an obvious future user (`ke review archive`
  already archives *queued* items).

## Alternatives considered

**Keep both and document the difference harder.** ADR-0029 already tried that.
Two similarly-named states distinguished only by a paragraph is a bug waiting for
a tired reader.

**Drop `status: replaced` and keep the lifecycle stage.** Rejected: `status` is
part of the published schema, is user-visible, and pairs with `replaced_by`,
which is user-owned. The lifecycle value was the newer and less-used of the two.

**Leave it until something needs it.** That is what M3 did, and it cost a
glossary entry, an ADR paragraph, and this ADR. Speculative enum values are
cheap to add and awkward to remove.

## Lesson

`SUPERSEDED` and `ARCHIVED` were both defined in M3 without users. One turned
out to be redundant. Defining a state before anything produces it means guessing
at semantics — and ADR-0029 said as much at the time, in the Negative section
nobody was obliged to act on. **Acting on a recorded weakness at the first
milestone that touches it is what stops it becoming permanent.**
