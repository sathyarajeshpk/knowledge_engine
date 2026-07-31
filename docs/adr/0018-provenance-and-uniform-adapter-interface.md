# ADR-0018: Provenance on every item, behind one adapter interface

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

M1 introduces several adapters — HTML tables, RSS, Atom, GitHub commits — with
more to come. Two risks appear together.

**Source-shaped leakage.** If downstream stages can tell an RSS item from a
scraped table row, dedupe, minting, classification and storage each grow a
special case per adapter, and adding a source stops being free.

**Untraceable parser breaks.** Source validation showed how brittle these sources
are: the two "official" feeds return 403, and the primary source is a web page
whose markup Microsoft can change without warning. When markup changes, the items
produced afterwards are subtly *wrong* rather than absent — the failure mode no
health check catches, because the pipeline keeps returning plausible items.
Without a record of which parser produced what, the only remedy is re-verifying
the entire pack.

## Decision

**One interface.** Every discovery adapter implements exactly:

```python
def discover(self) -> list[RawItem]: ...
```

`RawItem` is the only type an adapter may return. No stage after discovery learns
where an item came from — except through provenance, which is data rather than
control flow.

**Provenance on every item.** A required `Provenance` record captures
`adapter_type`, `source_name`, `discovered_at` (UTC), `extraction_method`,
`parser_version`, `selector`, `run_id` and `source_role`. It travels into the
stored knowledge object and is engine-owned.

`parser_version` is incremented by hand whenever an adapter's extraction logic
changes.

## Consequences

### Positive
- **Downstream stages are source-agnostic.** Adding an adapter changes nothing
  after discovery.
- **Parser breaks become surgical.** "Find every object produced by the HTML
  table parser at version 1" is a filter, not an audit.
- **`selector` records the actual anchor used**, so a markup change can be
  diagnosed from the stored data without re-fetching.
- **`run_id` correlates** object, revision, event log and run log — any run is
  fully reconstructable.
- **`source_role` shows which fallback link produced an item**, so knowledge
  gathered from a degraded chain is identifiable rather than indistinguishable.
- Testing an adapter means asserting on returned `RawItem`s. Nothing else needed.

### Negative
- Every adapter must construct a full provenance record — more boilerplate per
  adapter, deliberately, since the alternative is optional provenance that is
  omitted exactly when it matters.
- `parser_version` is manual and can be forgotten. A reviewer check; automating
  it would mean hashing adapter source, which is fragile.
- Adds roughly eight lines to every `metadata.yaml`.

### Neutral
- `RawItem` becoming the sole contract means changing it touches every adapter.
  That is the cost of a narrow interface, and the reason to get it right in M1.

## Alternatives considered

**Adapters return their own types.** Rejected: pushes source knowledge downstream
and makes each new source a change to dedupe, minting and storage.

**Provenance optional, added when needed.** Rejected: it would be missing from
exactly the historical items a future investigation needs. Provenance is only
useful if it is universal.

**Log provenance to the run log rather than the object.** Rejected: run logs
rotate and are summarised; the object outlives them. Provenance belongs with the
thing it describes.

**Derive `parser_version` from a hash of the adapter source.** Rejected: changes
on every cosmetic edit, so it would stop meaning "the extraction logic changed".
