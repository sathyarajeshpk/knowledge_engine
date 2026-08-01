# ADR-0032: Each state file gets its own failure policy

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M2

## Context

M2 introduced three state files under `state/`. The obvious approach — handle
corruption the same way everywhere — is wrong in both possible directions: fail
loudly on all three and a corrupt cache stops the engine; degrade on all three
and a corrupt registry reuses permanent identifiers.

What differs between them is **what is lost when the file is lost**.

## Decision

| File | Holds | Damaged → | Why |
|---|---|---|---|
| `seen.json` | Dedup keys for previous runs | **Degrade**: start empty | Rebuildable from the objects themselves. Worst case is re-examining knowledge we already have |
| `id-registry.json` | Per-month counters, ID → path | **Fail loudly** | Minting against a partially-read registry reuses a Feature ID, and a reused ID can never be undone |
| `review-queue.json` | Held-back items, original discovery dates, human decisions | **Fail loudly** | Neither the decisions nor the first-seen dates exist anywhere else |

The ID registry additionally **validates its own consistency on load**: every
recorded ID must parse, and must not exceed its month's counter. A registry that
fails that check is refused rather than repaired, because the repair would be a
guess about which IDs are real.

## Consequences

### Positive
- The dangerous files cannot be silently worked around.
- The recoverable file cannot stop a harvest.
- Each policy is justified by what the file uniquely holds, so a future state
  file has a question to answer rather than a convention to copy.

### Negative
- Three behaviours to remember instead of one.
- A corrupt `review-queue.json` blocks harvesting entirely, including for items
  that would have minted fine. Deliberate: continuing would silently re-queue
  everything with today's date, quietly shifting Feature ID months.
- No repair tooling yet. `ke doctor` is a candidate for M9.

## Alternatives considered

**Uniform fail-fast.** A corrupt dedup cache — the one genuinely rebuildable
file — would stop the engine for no reason.

**Uniform degrade.** A corrupt registry would silently reuse Feature IDs. This is
the worst outcome available in the entire system.

**Auto-repair the registry by scanning knowledge objects.** Attractive, and it
guesses: an object present on disk but absent from the registry might be a
crashed write or a hand-added file, and those want different treatment. Better
to fail and let a human look. Revisit with `ke doctor`.
