"""Running every source, honouring fallback chains, recording health.

This is the only place that knows about fallback chains and failure handling.
Adapters just fetch and parse; they never decide what happens when they fail.

The rule this module enforces, from ADR-0019:

    A failed source never fails the run.

Harvesting continues from every healthy source and each failure is recorded, so
one dead feed cannot stop the pipeline -- and, more importantly, cannot stop the
run-log commit that keeps the weekly cron alive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ke.clock import Clock
from ke.models import (
    AdapterType,
    HealthState,
    RawItem,
    SourceAttempt,
    SourceHealth,
    SourceRole,
)
from ke.sources.base import (
    Fetcher,
    HttpFetcher,
    SourceDefinition,
    SourceError,
    sort_items,
)
from ke.sources.feed import FeedSource
from ke.sources.html_table import HtmlTableSource

#: Adapter type -> implementation. Adding a source type is one entry here plus
#: one module; nothing downstream changes (ADR-0018).
ADAPTERS = {
    AdapterType.HTML: HtmlTableSource,
    AdapterType.RSS: FeedSource,
    AdapterType.ATOM: FeedSource,
    AdapterType.GITHUB_COMMITS: FeedSource,
}


@dataclass
class ReviewItem:
    """Raised when every link in a fallback chain failed.

    The point of this type is that it is *not* an empty list. A chain that fails
    completely must produce something a human will see, because "no updates" and
    "we could not look" must never be indistinguishable.
    """

    source_name: str
    url: str
    reason: str
    attempted_roles: tuple[SourceRole, ...] = ()


@dataclass
class DiscoveryResult:
    """Everything one discovery pass produced."""

    items: list[RawItem] = field(default_factory=list)
    attempts: list[SourceAttempt] = field(default_factory=list)
    health: dict[str, SourceHealth] = field(default_factory=dict)
    review_items: list[ReviewItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def failed_sources(self) -> list[str]:
        return sorted(
            {attempt.source_name for attempt in self.attempts if not attempt.ok}
        )


def build_source(
    definition: SourceDefinition,
    fetcher: Fetcher,
    clock: Clock,
    *,
    max_summary_words: int,
):
    adapter = ADAPTERS.get(definition.adapter)
    if adapter is None:
        raise SourceError(f"no adapter registered for {definition.adapter!r}")
    return adapter(definition, fetcher, clock, max_summary_words=max_summary_words)


def discover_all(
    definitions: list[SourceDefinition],
    *,
    clock: Clock,
    fetcher: Fetcher | None = None,
    known_health: dict[str, SourceHealth] | None = None,
    max_summary_words: int = 120,
) -> DiscoveryResult:
    """Run every pollable source and its fallback chain.

    `known_health` carries the state persisted from previous runs. It is what
    makes parser-break detection possible: without a baseline the engine has no
    way to know that twenty items dropping to zero is abnormal.
    """
    fetcher = fetcher or HttpFetcher()
    known_health = known_health or {}
    result = DiscoveryResult()

    for definition in definitions:
        if not definition.is_pollable:
            # Retained for provenance, deliberately not polled (ADR-0024).
            result.skipped.append(definition.name)
            continue
        _discover_chain(
            definition, result, clock, fetcher, known_health, max_summary_words
        )

    result.items = sort_items(result.items)
    return result


def _discover_chain(
    definition: SourceDefinition,
    result: DiscoveryResult,
    clock: Clock,
    fetcher: Fetcher,
    known_health: dict[str, SourceHealth],
    max_summary_words: int,
) -> None:
    """Try a source, then each fallback in turn, stopping at the first success."""
    chain = (definition, *definition.fallbacks)
    tried: list[SourceRole] = []
    last_reason = "no source in the chain was attempted"

    for link in chain:
        tried.append(link.role)

        if link.role is SourceRole.MANUAL_REVIEW:
            # A terminal marker rather than a real source: reaching it means
            # every fetchable link already failed.
            break

        attempt, items, reason = _attempt(link, clock, fetcher, max_summary_words)
        result.attempts.append(attempt)

        health = known_health.get(link.name, SourceHealth(source_name=link.name))
        result.health[link.name] = health.record(attempt)

        if attempt.ok:
            result.items.extend(items)
            return
        last_reason = reason or "unknown failure"

    result.review_items.append(
        ReviewItem(
            source_name=definition.name,
            url=definition.url,
            reason=last_reason,
            attempted_roles=tuple(tried),
        )
    )


def _attempt(
    definition: SourceDefinition,
    clock: Clock,
    fetcher: Fetcher,
    max_summary_words: int,
) -> tuple[SourceAttempt, list[RawItem], str | None]:
    """One fetch-and-parse, converting any failure into a recorded attempt."""
    started = clock.now()
    try:
        source = build_source(
            definition, fetcher, clock, max_summary_words=max_summary_words
        )
        items = source.discover()
    except SourceError as exc:
        return (
            SourceAttempt(
                source_name=definition.name,
                run_id=clock.run_id(),
                attempted_at=started,
                ok=False,
                role=definition.role,
                failure_reason=str(exc),
            ),
            [],
            str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        # An adapter bug must be recorded and survived, not allowed to end the
        # run. A crash here would take every other source down with it.
        reason = f"adapter error: {type(exc).__name__}: {exc}"
        return (
            SourceAttempt(
                source_name=definition.name,
                run_id=clock.run_id(),
                attempted_at=started,
                ok=False,
                role=definition.role,
                failure_reason=reason,
            ),
            [],
            reason,
        )

    return (
        SourceAttempt(
            source_name=definition.name,
            run_id=clock.run_id(),
            attempted_at=started,
            ok=True,
            role=definition.role,
            http_status=200,
            items_discovered=len(items),
        ),
        items,
        None,
    )


def health_summary(health: dict[str, SourceHealth]) -> dict[HealthState, list[str]]:
    """Group source names by state, for `ke health` and the weekly digest."""
    grouped: dict[HealthState, list[str]] = {state: [] for state in HealthState}
    for name, entry in sorted(health.items()):
        grouped[entry.state].append(name)
    return grouped
