# ADR-0022: Same inputs produce byte-identical outputs

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

Every artefact this engine produces is committed to Git: knowledge objects,
indexes, the ID registry, the event log, digests, health state.

Git diffs are how a human reviews what the weekly run did. If unchanged content
serialises differently between runs — dictionary iteration order, unsorted source
output, unstable timestamps — the weekly commit fills with churn, real changes
become invisible inside it, and the review that keeps the automation honest stops
happening.

Non-determinism also makes idempotency untestable. "Running harvest twice
produces no new objects" is only checkable if a second run genuinely produces the
same bytes.

## Decision

**Same inputs must always produce byte-identical outputs.**

Concretely:

- Adapters sort before returning (`sort_items`: publication date, then identity
  key, undated last). Never the order the source happened to present things in.
- Every generated file — index, registry, event batch, digest — is written in a
  defined order, never in dictionary insertion order.
- `canonical_url` sorts surviving query parameters, so two URLs differing only in
  parameter order canonicalise identically.
- Time comes from an injected clock (ADR-0021), so timestamps are inputs rather
  than ambient state.
- YAML is written with a fixed key order (`sort_keys=False` plus an explicit
  field order in `to_metadata_dict`).

## Consequences

### Positive
- **Weekly diffs show only real changes**, so review stays possible.
- **Idempotency becomes testable**, which is M2's main acceptance criterion.
- **Deterministic ordering is a prerequisite for replay** (ADR-0025): comparing a
  replayed run with the original is only meaningful if identical inputs must
  produce identical bytes.
- Merge conflicts become rarer and easier to resolve.

### Negative
- Every writer must sort, and forgetting is silent until someone notices a noisy
  diff. Mitigated by centralising ordering in `sort_items` and the serialisers
  rather than leaving it to each call site.
- Sorting costs time proportional to `n log n`. Irrelevant at this scale.
- Sorted output sometimes reads less naturally than source order — a table's own
  ordering may carry editorial meaning that sorting discards. Accepted: the
  section heading is captured as a tag, so grouping survives.

### Neutral
- Applies to generated artefacts, not to user-authored files. A human writing
  notes may order them however they like.

## Alternatives considered

**Sort only at display time.** Rejected: the committed bytes are the artefact,
and they are what diffs compare.

**Rely on Python 3.7+ dict insertion order.** Rejected: it makes output depend on
the order things were *added*, which depends on source order, which is exactly
the instability being removed.

**Normalise diffs with a pre-commit filter.** Rejected: hides the problem from
Git while leaving the files themselves unstable.
