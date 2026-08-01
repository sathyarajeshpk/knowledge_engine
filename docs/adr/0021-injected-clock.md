# ADR-0021: Time is injected, never read from the system clock

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

Discovery stamps every item with a discovery date, a discovery timestamp and a
run identifier. Revisions, events and the run log all carry timestamps too.

If each component calls `datetime.now()` for itself, three things become
impossible: tests that assert on time-dependent output without mocking,
byte-identical repeat runs, and any future replay of an archived snapshot *as it
would have been processed at the time*.

The last is the one that forces the decision now. Replay (ADR-0025) requires the
pipeline to accept a historical instant. A component that reads the system clock
internally can never be replayed, and retrofitting injection once six modules do
it is invasive — every call site, every signature, every test.

## Decision

**No module in `engine/ke` may call `datetime.now()` or `date.today()`, except
`ke.clock`.** Every component that needs the time receives a `Clock`.

`Clock` is a `Protocol` with `now()`, `today()` and `run_id()`. Two
implementations: `SystemClock` reads the real clock; `FrozenClock` is stuck at
one instant and rejects a naive datetime, because an ambiguous instant in a
history is worse than no history.

`run_id()` derives a stable identifier from the instant
(`run-2026-08-02T06-00-00Z`), so every artefact of a run can be correlated
without threading an extra parameter everywhere.

**Enforced, not trusted.** `test_no_engine_module_reads_the_clock_directly`
walks every module in the package and fails if one reads the clock. A convention
nobody checks stops being true.

## Consequences

### Positive
- Time-dependent behaviour is testable with a plain assertion, no mocking.
- Repeat runs with the same clock produce identical output, which is what makes
  deterministic ordering (ADR-0022) meaningful.
- Replay stays possible without a rewrite.
- One obvious place to look when a timestamp is wrong.

### Negative
- Every component that needs time grows a constructor parameter. Real
  boilerplate, accepted because the alternative is unfixable later.
- The enforcement test is a string search, so it would miss `from datetime
  import datetime as dt; dt.now()`. Good enough to stop the accident it is aimed
  at; not proof against determined circumvention.

### Neutral
- `Protocol` rather than a base class, so adapters depend on the shape.

## Alternatives considered

**Mock the clock in tests only.** Rejected: production code still reads the real
clock, so replay stays impossible and mocking spreads through every test.

**A module-level `now()` function that tests monkeypatch.** Rejected: global
mutable state, and concurrent runs would share it.

**Pass a bare `datetime` instead of a `Clock`.** Rejected: a run needs *several*
correlated values — instant, date, run id — and deriving them separately at each
call site invites them to disagree.
