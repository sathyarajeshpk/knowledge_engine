# ADR-0041: Search scans the objects rather than maintaining an index

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M7

## Context

`ke search` needs to filter 222 knowledge objects — soon several thousand across
several packs — by tier, category, tag, date, learning state and free text.

The conventional answer is a search index: build it during the harvest, store it
in `state/`, query it from the CLI. It is what every system of this shape does
once it gets big enough, and it is fast.

## Decision

**`search` reads every `metadata.yaml` and filters in memory. There is no index,
no cache and no `search.json`.**

Three properties follow:

* **It cannot be stale.** The answer comes from the objects themselves, so it
  describes what is actually in the repository — including anything added or
  edited by hand, which is a supported way to work here (ADR-0002).
* **There is nothing to invalidate.** No rebuild step, no staleness check, no
  "run `ke reindex` after editing", no failure mode where the index and the
  objects disagree and the search confidently returns the wrong set.
* **It is one code path.** `retrieve`, `artifacts`, `reviewq`, `digest` and the
  indexer all read objects the same way.

At the current size a full scan takes milliseconds. That is not the argument,
though — it is what makes the argument affordable.

## Consequences

**A derived structure that can disagree with its source is a bug waiting for a
quiet week.** This repository has already produced two of them: the 222 orphaned
`feature.md` files in M2, and the registry path mismatch in M3. Both were cases
of two things that should have agreed, not agreeing. Not building a third is
worth more than the milliseconds.

**Cost is linear in pack size.** Every search reads every object. At 222 objects
this is imperceptible; at 50,000 it would not be.

**When that day comes, the fix is not this decision reversed.** It is a cache
built during `rebuild_indexes` and *thrown away whenever it might be stale* — a
different design, with different invariants, and it should be a different ADR
written when there is evidence rather than in anticipation. Building it now would
mean maintaining an invalidation strategy for a problem nobody has.

**Free-text search is a substring match, not a ranked one.** Case- and
punctuation-insensitive over title, category and tags — so `direct lake` finds
the tag `direct-lake` — but with no scoring. Ranking would invent a notion of
relevance the engine cannot justify, and a wrong ranking is worse than none
because it hides things convincingly rather than obviously.

**Ordering is deterministic to the last tiebreak:** tier, then recency, then
Feature ID. Search output gets pasted into notes and diffed, so a result set that
reorders itself between identical runs produces a diff that means nothing
(ADR-0022).

## Alternatives considered

**A JSON index rebuilt each harvest.** Fast, and the obvious engineering answer.
Rejected for the invalidation problem: a hand-edit between harvests makes the
index wrong, and a search that silently returns a stale set is exactly the class
of confidently-wrong output this project keeps finding and fixing.

**SQLite FTS.** Genuinely good at this. Rejected on two grounds: it is a database,
which the project excludes by design (ADR-0003), and it would become a file that
must be kept in sync with the Markdown that is supposed to be the source of
truth.

**Filtering by shelling out to `grep`/`ripgrep`.** Fast and dependency-free.
Rejected because the filters are semantic — `--tier 1`, `--stale`, date ranges —
and expressing those as regular expressions over YAML would be both fragile and
unreadable.
