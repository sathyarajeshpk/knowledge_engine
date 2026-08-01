"""Adapter for RSS and Atom feeds.

Handles both formats through `feedparser`, which is the one place a dependency
earns its keep: real-world feeds are inconsistent in ways that a hand-rolled
parser discovers slowly and painfully.

Used for the GitHub docs-commit Atom feeds and the Fabric corporate blog RSS.
Note that validation showed the blog feed ships **29,000-character full
articles**, so truncation is mandatory rather than tidy -- ADR-0003 forbids
storing full third-party article text, and this is where that rule is enforced
at ingest.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ke.clock import Clock
from ke.acquisition.identity import compute_identity
from ke.models import (
    AdapterType,
    DateConfidence,
    DatePrecision,
    ExtractionMethod,
    Provenance,
    RawItem,
    SourceRepresentation,
)
from ke.normalize import canonical_url, html_to_text, truncate_summary
from ke.acquisition.sources.base import Fetcher, SourceDefinition, SourceError, sort_items

DEFAULT_MAX_SUMMARY_WORDS = 120


class FeedSource:
    """Discovery adapter for an RSS or Atom feed."""

    def __init__(
        self,
        definition: SourceDefinition,
        fetcher: Fetcher,
        clock: Clock,
        *,
        max_summary_words: int = DEFAULT_MAX_SUMMARY_WORDS,
    ) -> None:
        self.definition = definition
        self._fetcher = fetcher
        self._clock = clock
        self._max_summary_words = max_summary_words

    def discover(self) -> list[RawItem]:
        try:
            import feedparser
        except ImportError as exc:  # pragma: no cover - declared in pyproject
            raise SourceError("feedparser is not installed") from exc

        result = self._fetcher.fetch(self.definition.url)
        parsed = feedparser.parse(result.body)

        if parsed.get("bozo") and not parsed.entries:
            reason = type(parsed.get("bozo_exception")).__name__
            raise SourceError(f"response did not parse as a feed ({reason})")

        if not parsed.entries:
            raise SourceError("feed parsed but contained no entries")

        items = [
            item
            for entry in parsed.entries
            if (item := self._entry_to_item(entry)) is not None
        ]
        return sort_items(items)

    def _entry_to_item(self, entry) -> RawItem | None:
        title = " ".join((entry.get("title") or "").split())
        link = entry.get("link") or ""
        if not title and not link:
            return None

        published, precision, confidence = self._entry_date(entry)

        # Prefer the longest body the feed offers, then truncate hard. A feed
        # that ships whole articles must not put whole articles in the pack.
        body = entry.get("summary", "") or ""
        for content in entry.get("content", []) or []:
            candidate = content.get("value", "") or ""
            if len(candidate) > len(body):
                body = candidate
        summary = truncate_summary(html_to_text(body) or title, self._max_summary_words)

        canonical = canonical_url(link) if link else None
        # Feeds usually publish a stable per-entry id; prefer it over the URL
        # only when there is no URL, since a URL is checkable by a human and an
        # opaque feed id is not.
        identity = compute_identity(
            canonical_url=canonical,
            source_identifier=entry.get("id") or entry.get("guid"),
            title=title,
            summary=summary,
        )

        adapter = (
            AdapterType.ATOM
            if self.definition.adapter is AdapterType.ATOM
            else self.definition.adapter
        )

        return RawItem(
            source_name=self.definition.name,
            source_url=canonical or self.definition.url,
            # A feed entry's link is its own permalink: one entry, one
            # announcement. This is why feeds never produced the merging defect.
            announcement_url=canonical,
            source_authority=self.definition.authority,
            title=title or link,
            summary=summary,
            discovered_date=self._clock.today(),
            published_date=published,
            date_confidence=confidence,
            date_precision=precision,
            raw_tags=tuple(
                sorted(tag.get("term", "") for tag in (entry.get("tags") or []) if tag.get("term"))
            ),
            identity=identity,
            provenance=Provenance(
                source_name=self.definition.name,
                source_representation=(
                    SourceRepresentation.ATOM
                    if adapter in (AdapterType.ATOM, AdapterType.GITHUB_COMMITS)
                    else SourceRepresentation.RSS
                ),
                adapter_type=adapter,
                discovered_at=self._clock.now(),
                extraction_method=ExtractionMethod.FEED_ENTRY,
                parser_version=self.definition.parser_version,
                identity_basis=identity.basis,
                identity_key=identity.key,
                selector="entry",
                run_id=self._clock.run_id(),
                source_role=self.definition.role,
            ),
        )

    @staticmethod
    def _entry_date(entry) -> tuple[date | None, DatePrecision, DateConfidence]:
        """Feed timestamps are precise to the second, so day precision is right.

        `published` is preferred over `updated`: we want when the item was
        announced, not when someone last touched it.
        """
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not stamp:
            return None, DatePrecision.DAY, DateConfidence.INFERRED
        moment = datetime(*stamp[:6], tzinfo=timezone.utc)
        return moment.date(), DatePrecision.DAY, DateConfidence.EXACT
