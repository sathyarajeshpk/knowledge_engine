# ADR-0031: The order of pipeline stages is a safety property

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M2

## Context

`ke harvest` runs six stages. Several orderings would "work" in the sense of
producing output; only one fails safely when the run dies halfway through — which
it will, eventually, on a network blip or a full disk.

Feature IDs are permanent and never reused (ADR-0005), so the cost of the
failure modes is wildly asymmetric.

## Decision

**1. Deduplicate before minting.** Minting is irreversible; deduplication is the
last point at which noticing "we already have this" is free.

**2. Gate after deduplication.** Grading an item we are not going to store is
wasted work, and worse, it would put already-known items into the review queue.

**3. Save the ID registry *after* objects are on disk.** The two failure modes
are not equivalent:

* registry saved first, crash → an ID recorded with no object. A permanent hole:
  the ID is consumed, nothing occupies it, and no later run will fill it.
* objects written first, crash → objects on disk with no registry entry. The
  next run re-reads them, and `ke validate` reports the gap. Repairable.

**Fail in the recoverable direction.**

**4. Append the run log unconditionally**, including on a zero-item run. GitHub
disables a scheduled workflow after 60 days without commit activity. A pack that
harvests nothing for two quiet months would otherwise stop being harvested at
all — the engine dying of silence.

**5. Rebuild indexes last, from disk.** Indexes are derived data. Building them
from what is actually stored — rather than from what this run happened to mint —
means a hand-edited or hand-added object appears in them too.

## Consequences

### Positive
- A mid-run crash is recoverable in every stage.
- The weekly cron cannot be disabled by a quiet period.
- Indexes reflect the repository, not the run.

### Negative
- **ID sequences can contain gaps.** `mint()` advances the counter before the
  write succeeds, so a failure skips a number. Deliberate — gaps are safe, reuse
  is not — but it means `MSF-2026-08-042` missing is not evidence of deletion.
- The registry is written once at the end, so a very long run holds all minting
  state in memory. Fine at hundreds of objects; not a design for millions.

## Alternatives considered

**Transactional write across all state files.** Correct, and disproportionate:
it would mean a write-ahead log and recovery code for a weekly job whose worst
failure is already recoverable.

**Mint lazily, after the write succeeds.** Removes ID gaps, but requires knowing
the object's path before its ID exists — and the path contains the ID.

**Skip the run log when nothing was found.** Tidier output, and it would silently
kill the scheduled workflow within two months.
