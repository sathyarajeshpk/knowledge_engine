# ADR-0005: Date-based Feature IDs with per-month counters

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

Every knowledge object needs a permanent identifier. `CLAUDE.md` requires unique
Feature IDs and chronological order. The identifier will appear in directory
names, indexes, digests, relationship references and anything the user writes
down, so it must be:

- **Permanent** — never changes, never reused.
- **Human-readable** — you can say it out loud and recognise it in a list.
- **Sortable** — sorting IDs should give chronological order.
- **Mintable offline** — no coordination service.
- **Meaningful** — ideally tells you something without a lookup.

A simple monotonic counter (`MSF-0001`) satisfies the first four but not the
last, and it has a subtler problem: backfilling historical content forces old
material to take high numbers, so ID order stops matching chronology.

## Decision

Feature IDs take the form `<PREFIX>-<YYYY>-<MM>-<NNN>`, e.g. `MSF-2026-04-001`.

The month segment comes from the **publication month** when a reliable date was
parsed, and from the **discovery month** otherwise. `date_confidence` records
which (`exact` or `inferred`), and both `published_date` and `discovered_date`
are always stored.

Counters are held **per month** in `state/id-registry.json` as
`{"2026-04": 7, "2025-11": 4}`.

Once assigned, a Feature ID **never changes and is never reused**, including for
objects later marked `replaced`. A month exceeding 999 objects widens the
sequence to four digits; existing IDs are never renumbered.

## Consequences

### Positive
- **The ID carries information.** `MSF-2026-04-001` tells you the pack, the era,
  and that it was the first item that month — no lookup required.
- **Chronological sorting is free.** `FeatureId` uses `@dataclass(order=True)`
  with fields declared prefix, year, month, sequence, so `sorted(ids)` is
  chronological. This is how "always preserve chronological order" is satisfied.
- **Backfill is clean.** Ingesting a November 2025 article mints
  `MSF-2025-11-00N` against that month's counter without disturbing April 2026.
  A global counter would force the past to take the highest numbers.
- **Minting is offline and coordination-free** — read the month's counter,
  increment, write.
- **The directory path is derivable from the ID** (`knowledge/2026/04/...`), and
  `ke validate` checks the two agree, so a misfiled object is caught.

### Negative
- **The registry must persist counters for every month forever.** A month whose
  objects are all superseded still cannot reuse its numbers. The file grows by
  one small entry per month — negligible.
- **A counter behind reality would cause collisions.** This is the specific
  failure `REG002` exists to prevent: if the counter for a month is lower than
  the highest sequence actually in use, the next mint would eventually collide.
  A counter *ahead* of reality is explicitly allowed and tested, because gaps are
  harmless when IDs are never reused.
- **Publication dates are not always reliable.** Hence the fallback and the
  `date_confidence` field. An item ingested with `inferred` dating that later
  turns out to have an earlier publication date keeps its original ID — the ID is
  permanent even when the date basis turns out to be imperfect.
- **The 999/month ceiling** needed an explicit answer. Widening to four digits is
  handled in the regex (`\d{3,}`) and tested.

### Neutral
- Slightly longer than a bare counter. Worth it.
- Prefixes are permanent per pack; changing `id_prefix` would orphan every ID,
  which is stated in `pack.yml` where someone might otherwise tidy it.

## Alternatives considered

**Global monotonic counter (`MSF-0001`).** Simplest. Rejected: backfill breaks
chronological ordering, and the ID carries no information.

**UUIDs.** Guaranteed unique, no coordination. Rejected: unreadable, unsortable,
unmemorable, and they make directory names hostile. Uniqueness was never the hard
part.

**Slug-only IDs (`direct-lake-ga`).** Very readable. Rejected: not unique across
time (features get revisited), and titles change, which would force either a
rename — violating path permanence — or a mismatch.

**Full date (`MSF-2026-04-15-001`).** More precise. Rejected: the day adds
precision nobody needs, lengthens every reference, and makes counters
per-day — many with a single item. Month is the natural granularity for a weekly
pipeline.

**Content hash as the ID.** Stable and derivable. Rejected: changes when content
changes, which is exactly when the ID must *not* change (ADR-0009), and it is
unreadable.
