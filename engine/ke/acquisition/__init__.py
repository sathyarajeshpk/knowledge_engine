"""Acquisition: getting knowledge from a source to a gated, identified item.

This is a **subsystem with a deliberate boundary**, not just a folder. It is the
part of the engine that will be reused unchanged when knowledge starts arriving
from APIs, PDFs, videos or another documentation system — so it must not learn
anything about what happens to knowledge afterwards.

    fetch → parse → normalise → identify → grade → gate
                                                     │
                              storage, classification, indexing (M2+)

## The two ports

**In — the adapter contract.** One method, one return type:

```python
class Source(Protocol):
    def discover(self) -> list[RawItem]: ...
```

Adding a source type is: one module implementing that, one entry in `ADAPTERS`,
one `adapter:` block in `pack.yml`. Nothing downstream changes. A PDF adapter and
an HTML adapter are indistinguishable to everything past this line.

**Out — `DiscoveryResult`.** Items already graded and split:

```python
result.mintable      # cleared the gate — M2 may mint permanent Feature IDs
result.needs_review  # queued, never dropped
result.collisions    # one identity, several distinct features
result.health        # per-source state
result.review_items  # a whole fallback chain failed
```

## The rules that keep the boundary clean

1. **Acquisition imports downward only** — `models`, `normalize`, `clock`. Never
   storage, classification, indexing or digest. Enforced by a test, not trust.
2. **Adapters fetch and parse. They never decide what happens on failure** —
   they raise `SourceError` and `discover` decides. One dead source cannot end a
   run because that decision lives in exactly one place.
3. **No source-specific logic outside an adapter.** Anything that knows about a
   particular vendor's HTML, a particular host, or a particular date format
   belongs either in that adapter or in `pack.yml` as data.
4. **Everything except fetching is pure.** The clock and the fetcher are
   injected; the rest is deterministic, which is what makes a run replayable and
   the whole subsystem testable offline.

## Known debt against rule 3

`identity.TITLE_NOISE` is Microsoft-flavoured ("preview", "generally
available"…). It lives in code rather than `pack.yml` because ADR-0023 judged a
shared vocabulary safer than a per-pack one while there is one vendor. A second
vendor — or a video or PDF source with different title conventions — is the
trigger to move it into pack configuration.
"""

from ke.acquisition.confidence import Collision, assess, collisions, summarise
from ke.acquisition.discover import (
    ADAPTERS,
    DiscoveryResult,
    ReviewItem,
    build_source,
    discover_all,
    health_summary,
)
from ke.acquisition.identity import compute_identity, normalise_title
from ke.acquisition.sources.base import (
    FetchResult,
    Fetcher,
    HttpFetcher,
    SourceDefinition,
    SourceError,
    sort_items,
)

__all__ = [
    "ADAPTERS",
    "Collision",
    "DiscoveryResult",
    "FetchResult",
    "Fetcher",
    "HttpFetcher",
    "ReviewItem",
    "SourceDefinition",
    "SourceError",
    "assess",
    "build_source",
    "collisions",
    "compute_identity",
    "discover_all",
    "health_summary",
    "normalise_title",
    "sort_items",
    "summarise",
]
