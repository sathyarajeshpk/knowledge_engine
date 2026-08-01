# ADR-0025: Design for replayability without building it yet

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1 (constraints only) — implementation unscheduled

## Context

A desirable future capability: given an archived source snapshot, a parser
version and a timestamp, reproduce exactly the discovery output that was — or
would have been — produced at that moment.

That would make it possible to re-extract historical content after fixing a
parser bug, verify that a parser change does not silently alter old results, and
debug a past run without waiting for the source to misbehave again.

Replay is **not required in M1**, and building it now would mean designing an
archive format, a storage strategy and a CLI for a capability with no current
user. But several small decisions taken in M1 would make it impossible later, and
those cost nothing to get right now.

## Decision

**Do not build replay. Do not preclude it.** Four constraints, all of which M1
satisfies for reasons that stand on their own:

1. **Time is injected** (ADR-0021). Replay must process a snapshot as of the
   moment it was captured. A component reading the system clock can never be
   replayed.
2. **Fetching is injected.** Adapters receive a `Fetcher` protocol. A replay
   would serve archived documents through the same interface, and the adapter
   would not notice.
3. **Parsers are versioned** (ADR-0024). Replay is meaningless without knowing
   which extraction logic to apply.
4. **Output is deterministic** (ADR-0022). Comparing a replayed run with the
   original only means something if identical inputs must produce identical
   bytes.

Given those, replay reduces to a `Fetcher` that reads from an archive and a
`FrozenClock` — both already expressible.

**Deliberately not decided:** whether snapshots are stored at all, where, in what
format, and under what copyright constraints. ADR-0003 forbids storing full
third-party article text, so a snapshot archive is *not* obviously permissible
inside this repository and may have to live outside it. That question is
genuinely open and does not need answering to keep the option available.

## Consequences

### Positive
- The capability stays reachable at essentially zero present cost.
- Every constraint is justified independently, so none is speculative
  scaffolding.
- The `Fetcher` protocol already makes adapters testable offline, which is where
  its value is being earned today.

### Negative
- Injecting the fetcher adds a constructor parameter to every adapter. Small, and
  already paying for itself in the test suite.
- Recording an intention without a schedule risks it becoming aspiration. The
  constraints are the deliverable; the feature is explicitly unscheduled.

### Neutral
- Because the constraints hold, a future replay is additive: a new `Fetcher`, a
  `FrozenClock`, a CLI flag. No existing code changes.

## Alternatives considered

**Build replay now.** Rejected: no current user, and the storage and copyright
questions are unresolved. Building it would mean guessing at both.

**Ignore replay until it is asked for.** Rejected: the clock and fetcher
decisions are cheap now and invasive later, and the pipeline would have to be
rewritten to accept injected time once several modules read it directly.

**Archive every raw response as insurance.** Rejected on two grounds: repository
size, and ADR-0003's prohibition on storing full third-party article text. The
health baseline stores item counts, not bodies, for the same reason.
