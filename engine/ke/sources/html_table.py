"""Adapter for Microsoft Learn "What's New" pages.

This is M1's **primary** source. Validation found no reachable purpose-built
update feed: the official Fabric and Power BI RSS feeds return 403 from a
runner, and the only high-signal source is this web page. So the HTML adapter
carries the load that RSS was originally expected to.

The page structure, measured rather than assumed
(`docs/reviews/M1_SOURCE_VALIDATION.md`):

* `<h2>` sections are **feature areas** -- "Generally available features",
  "Features currently in preview", "Power BI", "Microsoft Fabric platform
  features" -- not months.
* Each section contains `<table>`s whose rows are individual updates.
* Dates appear **inside cells** as `Month YYYY`, not in headings.
* 23 tables, 730 links, 177 dates, `ms.date` current.

Identity comes from each row's link target, so a reordered or reworded table
does not mint new permanent Feature IDs (ADR-0023). That is the single most
important property of this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from ke.clock import Clock
from ke.identity import compute_identity
from ke.models import (
    AdapterType,
    ExtractionMethod,
    Provenance,
    RawItem,
)
from ke.normalize import (
    canonical_url,
    html_to_text,
    parse_date,
    truncate_summary,
)
from ke.sources.base import Fetcher, SourceDefinition, SourceError, sort_items

DEFAULT_MAX_SUMMARY_WORDS = 120


@dataclass
class TableRow:
    """One extracted row, before it becomes a `RawItem`."""

    section: str
    cells: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (href, text)

    @property
    def text(self) -> str:
        return " ".join(cell for cell in self.cells if cell).strip()


class WhatsNewParser(HTMLParser):
    """Walks `<h2>` sections and the `<table>` rows beneath them.

    Tracks the current section heading as it goes, so every row knows which
    feature area it belongs to -- that heading becomes the category signal M3
    will classify from.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[TableRow] = []
        self._section = ""
        self._heading_buf: list[str] = []
        self._in_heading = False
        self._in_row = False
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._row: TableRow | None = None
        self._href: str | None = None
        self._anchor_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("h1", "h2", "h3"):
            self._in_heading = True
            self._heading_buf = []
        elif tag == "tr":
            self._in_row = True
            self._row = TableRow(section=self._section)
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_buf = []
        elif tag == "a" and self._in_cell:
            self._href = attributes.get("href")
            self._anchor_buf = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3") and self._in_heading:
            heading = " ".join("".join(self._heading_buf).split())
            # "In this article" is Learn's table-of-contents heading, not a
            # feature area; letting it through would mislabel every row under it.
            if heading and heading.lower() != "in this article":
                self._section = heading
            self._in_heading = False
        elif tag == "a" and self._href is not None and self._row is not None:
            text = " ".join("".join(self._anchor_buf).split())
            self._row.links.append((self._href, text))
            self._href = None
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            if self._row is not None:
                self._row.cells.append(" ".join("".join(self._cell_buf).split()))
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row is not None and self._row.text:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_heading:
            self._heading_buf.append(data)
        if self._in_cell:
            self._cell_buf.append(data)
        if self._href is not None:
            self._anchor_buf.append(data)


class HtmlTableSource:
    """Discovery adapter for a Learn "What's New" style page."""

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
        """Fetch the page and return one `RawItem` per update row."""
        result = self._fetcher.fetch(self.definition.url)

        parser = WhatsNewParser()
        try:
            parser.feed(result.body)
        except Exception as exc:  # noqa: BLE001 - malformed markup is a source problem
            raise SourceError(f"could not parse HTML: {type(exc).__name__}: {exc}") from exc

        if not parser.rows:
            # Zero rows from a page that is supposed to be full of them is a
            # parser break, not a quiet week. Say so rather than returning [].
            raise SourceError(
                "no table rows found; the page structure has probably changed"
            )

        items = [
            item
            for row in parser.rows
            if (item := self._row_to_item(row, result.final_url)) is not None
        ]
        return sort_items(items)

    def _row_to_item(self, row: TableRow, base_url: str) -> RawItem | None:
        """Convert one row, or `None` if it is not an update.

        Header rows and rows without usable text are skipped. Skipping is safe
        here -- unlike dropping a *duplicate*, a header row carries no knowledge
        that could be lost.
        """
        published, precision, confidence = parse_date(row.text)

        # The first link with visible text is the update; later links are
        # usually "learn more" pointers to the same or related docs.
        target, title = None, ""
        for href, text in row.links:
            if text:
                target, title = urljoin(base_url, href), text
                break

        if not title:
            # No linked title: fall back to the longest non-date cell.
            candidates = [
                cell for cell in row.cells if cell and not parse_date(cell)[0]
            ]
            if not candidates:
                return None
            title = max(candidates, key=len)

        if len(title.split()) < 2:
            return None  # a stray cell, not an update

        summary_source = " ".join(cell for cell in row.cells if cell != title)
        summary = truncate_summary(
            html_to_text(summary_source) or title, self._max_summary_words
        )

        canonical = canonical_url(target) if target else None
        identity = compute_identity(
            canonical_url=canonical,
            title=title,
            summary=summary,
        )

        return RawItem(
            source_name=self.definition.name,
            source_url=canonical or canonical_url(base_url),
            source_authority=self.definition.authority,
            title=title,
            summary=summary,
            discovered_date=self._clock.today(),
            published_date=published,
            date_confidence=confidence,
            date_precision=precision,
            raw_tags=(row.section,) if row.section else (),
            identity=identity,
            provenance=Provenance(
                adapter_type=AdapterType.HTML,
                source_name=self.definition.name,
                discovered_at=self._clock.now(),
                extraction_method=ExtractionMethod.HTML_TABLE_ROW,
                parser_version=self.definition.parser_version,
                identity_basis=identity.basis,
                identity_key=identity.key,
                selector=f"h2[{row.section}] > table > tr",
                run_id=self._clock.run_id(),
                source_role=self.definition.role,
            ),
        )
