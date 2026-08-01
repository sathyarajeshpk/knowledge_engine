# ADR-0033: An update refreshes a subset of engine-owned fields, not all of them

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M3
**Relates to:** ADR-0008 (field ownership), ADR-0009 (update in place), ADR-0005 (permanent IDs)

## Context

ADR-0008 divides fields into engine-owned, engine-proposed and user-owned, and
M0 built the guard that stops the engine writing the last two. M3 is the first
milestone that actually *updates* an object, so the guard is finally load-bearing.

But "the engine may write engine-owned fields" turns out to be too coarse. Some
engine-owned fields are **not re-derivable from a later sighting**:

* `id` and `slug` define the object's permanent directory path (ADR-0006).
* `discovered_date` records when knowledge *first* appeared — a later sighting
  knows nothing about that.
* `provenance` describes the run that *created* the object.
* `revisions` is append-only and needs explicit handling, not replacement.

An update that refreshed all engine-owned fields would quietly rewrite every one
of those, moving object paths and resetting first-seen dates.

## Decision

Updates operate on an explicit allow-list, `UPDATABLE_FIELDS`, which is a
**strict subset** of `ENGINE_OWNED_FIELDS`:

```
title · source_url · announcement_url · published_date
date_confidence · date_precision · content_hash · url_hash
identity_confidence · reading_time
```

Everything else engine-owned is in `FROZEN_AFTER_MINT` and may not be updated.
Two import-time assertions enforce that the allow-list stays inside the
engine-owned set and disjoint from the frozen set — so the invariant fails at
import rather than at the next harvest.

**A revision is appended only when the change is material.** `identity_confidence`
moving on its own is explicitly not material: it is a per-run assessment
(ADR-0028) that legitimately shifts when an unrelated item stops sharing an
announcement. Recording a revision for it would fill the history with entries
that say nothing about the knowledge.

**Whitespace is not change.** `content_hash` normalises it, so a reflowed
paragraph produces no update and no revision.

**One sighting per identity per run.** The same feature is legitimately listed by
two sources with slightly different metadata. Both share an identity, so without
this rule both would run the update and the object would flip between their
renderings — twice per harvest, forever.

## Consequences

### Positive
- A Feature ID and its directory cannot move, structurally rather than by care.
- `discovered_date` keeps meaning "when this knowledge first appeared".
- A no-op harvest touches exactly one file: the run log.
- Adding a field to the model does not silently make it updatable — it must be
  named.

### Negative
- **Two lists to maintain.** A genuinely new engine-owned field that *should*
  refresh must be added to `UPDATABLE_FIELDS` explicitly, and forgetting means
  it silently never updates. The assertions catch contradictions, not omissions.
- `provenance` is frozen at first discovery, so an object found by the HTML
  adapter and later only present in the Markdown source still claims HTML
  provenance. Correct for "how this object came to exist"; potentially confusing
  for "where this knowledge is now". Revisit if it causes real confusion.
- Deliberately no deletion path: a feature that vanishes from its source keeps
  its object. That follows "never delete existing knowledge", but it means the
  pack accumulates items the source has retired, with nothing marking them.

## Alternatives considered

**Update every engine-owned field.** Simplest rule, and it rewrites `id`,
`slug`, `discovered_date` and `provenance` — the four things that must never
move.

**Derive updatability from a per-field flag on the model.** Cleaner in
principle. Rejected for now because it spreads the policy across 40 field
declarations, where the current two frozensets can be read in ten seconds.

**Replace the object wholesale and re-merge user fields afterwards.** Inverts the
safety property: the default becomes "overwrite" and preservation becomes a step
that can be skipped. The current direction — refuse by default, allow by name —
fails safe.
